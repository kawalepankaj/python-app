#!/usr/bin/env python3
"""
Kubernetes hard-down RCA and guarded auto-remediation using Amazon Bedrock.

The script:
1. Probes an application URL.
2. Discovers related Kubernetes ingress/service/deployment context.
3. Collects operational evidence from the cluster.
4. Sends the evidence to Amazon Bedrock for a structured RCA.
5. Optionally applies a small set of safe remediations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import boto3


DEFAULT_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
SAFE_FIX_CLASSES = {
    "scaled_to_zero",
    "crash_looping_pods",
    "deployment_rollout_stuck",
    "pods_not_ready_no_endpoints",
}


@dataclass
class CommandResult:
    command: List[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass
class ProbeResult:
    ok: bool
    status_code: Optional[int]
    error: Optional[str]
    body_excerpt: str
    latency_ms: int


@dataclass
class RemediationAction:
    action: str
    reason: str
    command: List[str]


class KubectlError(RuntimeError):
    pass


def run_command(
    command: Sequence[str],
    *,
    check: bool = True,
    timeout: int = 30,
) -> CommandResult:
    process = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        command=list(command),
        returncode=process.returncode,
        stdout=process.stdout.strip(),
        stderr=process.stderr.strip(),
    )
    if check and result.returncode != 0:
        rendered = " ".join(shlex.quote(part) for part in result.command)
        raise KubectlError(
            f"Command failed ({result.returncode}): {rendered}\n{result.stderr or result.stdout}"
        )
    return result


def run_kubectl(args: Sequence[str], *, check: bool = True, timeout: int = 30) -> CommandResult:
    return run_command(["kubectl", *args], check=check, timeout=timeout)


def kubectl_json(args: Sequence[str], *, timeout: int = 30) -> Dict[str, Any]:
    result = run_kubectl([*args, "-o", "json"], timeout=timeout)
    return json.loads(result.stdout or "{}")


def http_probe(url: str, timeout: int = 10) -> ProbeResult:
    start = time.time()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "k8s-bedrock-rca/1.0",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            latency_ms = int((time.time() - start) * 1000)
            return ProbeResult(
                ok=True,
                status_code=response.status,
                error=None,
                body_excerpt=body,
                latency_ms=latency_ms,
            )
    except urllib.error.HTTPError as exc:
        latency_ms = int((time.time() - start) * 1000)
        body = exc.read(512).decode("utf-8", errors="replace")
        return ProbeResult(
            ok=False,
            status_code=exc.code,
            error=str(exc),
            body_excerpt=body,
            latency_ms=latency_ms,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.time() - start) * 1000)
        return ProbeResult(
            ok=False,
            status_code=None,
            error=str(exc),
            body_excerpt="",
            latency_ms=latency_ms,
        )


def parse_host(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    return parsed.netloc.split("@")[-1].split(":")[0]


def match_ingresses(host: str) -> List[Dict[str, Any]]:
    ingresses = kubectl_json(["get", "ingress", "--all-namespaces"]).get("items", [])
    matches = []
    for ingress in ingresses:
        rules = ingress.get("spec", {}).get("rules", [])
        for rule in rules:
            if rule.get("host") == host:
                matches.append(ingress)
                break
    return matches


def pick_service_from_ingress(ingress: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    rules = ingress.get("spec", {}).get("rules", [])
    for rule in rules:
        for path in rule.get("http", {}).get("paths", []):
            backend = path.get("backend", {}).get("service", {})
            name = backend.get("name")
            if name:
                return ingress["metadata"]["namespace"], name
    return None


def service_selector(namespace: str, service_name: str) -> Dict[str, str]:
    service = kubectl_json(["get", "service", service_name, "-n", namespace])
    return service.get("spec", {}).get("selector", {}) or {}


def label_selector_to_string(selector: Dict[str, str]) -> str:
    return ",".join(f"{key}={value}" for key, value in selector.items())


def find_target_deployment(
    namespace: str,
    selector: Dict[str, str],
) -> Optional[str]:
    if not selector:
        return None
    selector_string = label_selector_to_string(selector)
    pods = kubectl_json(["get", "pods", "-n", namespace, "-l", selector_string]).get("items", [])
    replica_sets = set()
    for pod in pods:
        for owner in pod.get("metadata", {}).get("ownerReferences", []):
            if owner.get("kind") == "ReplicaSet":
                replica_sets.add(owner.get("name"))
    for rs_name in replica_sets:
        rs = kubectl_json(["get", "replicaset", rs_name, "-n", namespace])
        for owner in rs.get("metadata", {}).get("ownerReferences", []):
            if owner.get("kind") == "Deployment":
                return owner.get("name")
    deployments = kubectl_json(["get", "deployments", "-n", namespace]).get("items", [])
    for deployment in deployments:
        match_labels = deployment.get("spec", {}).get("selector", {}).get("matchLabels", {})
        if match_labels and all(selector.get(k) == v for k, v in match_labels.items()):
            return deployment["metadata"]["name"]
    return None


def summarize_pod(pod: Dict[str, Any]) -> Dict[str, Any]:
    container_states: List[Dict[str, Any]] = []
    for status in pod.get("status", {}).get("containerStatuses", []) or []:
        state = status.get("state", {})
        waiting = state.get("waiting")
        terminated = state.get("terminated")
        running = state.get("running")
        container_states.append(
            {
                "name": status.get("name"),
                "ready": status.get("ready"),
                "restartCount": status.get("restartCount"),
                "state": (
                    {"waiting": waiting}
                    if waiting
                    else {"terminated": terminated}
                    if terminated
                    else {"running": running}
                    if running
                    else {}
                ),
            }
        )
    return {
        "name": pod["metadata"]["name"],
        "phase": pod.get("status", {}).get("phase"),
        "podIP": pod.get("status", {}).get("podIP"),
        "hostIP": pod.get("status", {}).get("hostIP"),
        "conditions": pod.get("status", {}).get("conditions", []),
        "containerStatuses": container_states,
    }


def get_recent_events(namespace: str) -> List[Dict[str, Any]]:
    events = kubectl_json(["get", "events", "-n", namespace, "--sort-by=.lastTimestamp"]).get("items", [])
    trimmed = []
    for item in events[-20:]:
        trimmed.append(
            {
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "involvedObject": item.get("involvedObject", {}),
                "lastTimestamp": item.get("lastTimestamp") or item.get("eventTime"),
            }
        )
    return trimmed


def get_logs(namespace: str, pod_name: str) -> str:
    result = run_kubectl(
        ["logs", pod_name, "-n", namespace, "--tail=100"],
        check=False,
        timeout=30,
    )
    return result.stdout or result.stderr


def collect_cluster_context(url: str, namespace_hint: Optional[str]) -> Dict[str, Any]:
    host = parse_host(url)
    probe = http_probe(url)
    context: Dict[str, Any] = {
        "target_url": url,
        "host": host,
        "probe": probe.__dict__,
    }

    matched_ingresses = match_ingresses(host)
    if namespace_hint:
        matched_ingresses = [
            ingress for ingress in matched_ingresses if ingress["metadata"]["namespace"] == namespace_hint
        ] or matched_ingresses

    context["ingresses"] = [
        {
            "name": ingress["metadata"]["name"],
            "namespace": ingress["metadata"]["namespace"],
            "className": ingress.get("spec", {}).get("ingressClassName"),
            "loadBalancer": ingress.get("status", {}).get("loadBalancer", {}),
            "rules": ingress.get("spec", {}).get("rules", []),
        }
        for ingress in matched_ingresses
    ]

    if not matched_ingresses:
        return context

    service_ref = pick_service_from_ingress(matched_ingresses[0])
    if not service_ref:
        return context

    namespace, service_name = service_ref
    selector = service_selector(namespace, service_name)
    selector_string = label_selector_to_string(selector) if selector else None
    deployment_name = find_target_deployment(namespace, selector) if selector else None

    service = kubectl_json(["get", "service", service_name, "-n", namespace])
    endpoints = kubectl_json(["get", "endpoints", service_name, "-n", namespace])
    pods = (
        kubectl_json(["get", "pods", "-n", namespace, "-l", selector_string]).get("items", [])
        if selector_string
        else []
    )
    deployment = (
        kubectl_json(["get", "deployment", deployment_name, "-n", namespace])
        if deployment_name
        else None
    )

    pod_summaries = [summarize_pod(pod) for pod in pods]
    failing_pod = next(
        (
            pod for pod in pod_summaries
            if any(
                state.get("state", {}).get("waiting", {}).get("reason") in {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"}
                for state in pod.get("containerStatuses", [])
            )
        ),
        None,
    )

    pod_logs = get_logs(namespace, failing_pod["name"]) if failing_pod else ""

    context.update(
        {
            "namespace": namespace,
            "service": {
                "name": service_name,
                "selector": selector,
                "type": service.get("spec", {}).get("type"),
                "ports": service.get("spec", {}).get("ports", []),
            },
            "endpoints": endpoints.get("subsets", []),
            "deployment": (
                {
                    "name": deployment_name,
                    "replicas": deployment.get("spec", {}).get("replicas"),
                    "availableReplicas": deployment.get("status", {}).get("availableReplicas"),
                    "updatedReplicas": deployment.get("status", {}).get("updatedReplicas"),
                    "unavailableReplicas": deployment.get("status", {}).get("unavailableReplicas"),
                    "conditions": deployment.get("status", {}).get("conditions", []),
                }
                if deployment
                else None
            ),
            "pods": pod_summaries,
            "recent_events": get_recent_events(namespace),
            "failing_pod_logs": pod_logs,
        }
    )
    return context


def infer_local_incident_class(context: Dict[str, Any]) -> Tuple[str, str]:
    deployment = context.get("deployment") or {}
    endpoints = context.get("endpoints") or []
    pods = context.get("pods") or []

    if deployment.get("replicas") == 0:
        return "scaled_to_zero", "Deployment replica count is zero."

    for pod in pods:
        for container in pod.get("containerStatuses", []):
            waiting_reason = container.get("state", {}).get("waiting", {}).get("reason")
            if waiting_reason == "CrashLoopBackOff":
                return "crash_looping_pods", f"Pod {pod['name']} is in CrashLoopBackOff."
            if waiting_reason in {"ImagePullBackOff", "ErrImagePull"}:
                return "image_pull_failure", f"Pod {pod['name']} cannot pull its image."

    if deployment.get("unavailableReplicas") and not endpoints:
        return "pods_not_ready_no_endpoints", "Service has no endpoints and deployment has unavailable replicas."

    for condition in deployment.get("conditions", []):
        if condition.get("type") == "Progressing" and condition.get("status") == "False":
            return "deployment_rollout_stuck", condition.get("message", "Deployment rollout appears stuck.")

    if not context.get("ingresses"):
        return "ingress_not_found", "No ingress rule matched the host."

    if not endpoints:
        return "service_without_endpoints", "Service has no active endpoints."

    if not context.get("probe", {}).get("ok"):
        return "app_unreachable_unknown", "URL probe failed but local heuristics did not isolate a safe fix."

    return "healthy_or_transient", "The URL probe is currently healthy or the issue may be transient."


def build_bedrock_prompt(context: Dict[str, Any], local_class: str, local_reason: str) -> str:
    compact_context = json.dumps(context, indent=2)
    return textwrap.dedent(
        f"""
        You are a senior SRE and Kubernetes incident responder.
        Analyze the following outage evidence and return strict JSON.

        Goals:
        1. Identify the most likely root cause of the hard-down application URL.
        2. Rate confidence from 0.0 to 1.0.
        3. Recommend remediation steps.
        4. Mark whether a bounded auto-fix is safe.

        Local heuristic classification: {local_class}
        Local heuristic reason: {local_reason}

        Return JSON only with this schema:
        {{
          "incident_class": "string",
          "root_cause": "string",
          "confidence": 0.0,
          "blast_radius": "string",
          "recommended_actions": ["string"],
          "safe_auto_fix": true,
          "safe_auto_fix_reason": "string"
        }}

        Outage evidence:
        {compact_context}
        """
    ).strip()


def invoke_bedrock(model_id: str, region: str, prompt: str) -> Dict[str, Any]:
    client = boto3.client("bedrock-runtime", region_name=region)
    payload = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1200,
        "temperature": 0,
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    }
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(payload),
        contentType="application/json",
        accept="application/json",
    )
    raw_body = response["body"].read().decode("utf-8")
    body = json.loads(raw_body)
    text = "".join(part.get("text", "") for part in body.get("content", []))
    return parse_model_json(text)


def parse_model_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def choose_remediation(
    context: Dict[str, Any],
    local_class: str,
    bedrock_assessment: Dict[str, Any],
    allow_scale_from_zero: bool,
) -> Optional[RemediationAction]:
    namespace = context.get("namespace")
    deployment = context.get("deployment") or {}
    deployment_name = deployment.get("name")
    if not namespace or not deployment_name:
        return None

    if local_class not in SAFE_FIX_CLASSES:
        return None

    safe_from_model = bool(bedrock_assessment.get("safe_auto_fix"))
    if not safe_from_model:
        return None

    if local_class == "scaled_to_zero":
        if not allow_scale_from_zero:
            return None
        return RemediationAction(
            action="scale_deployment_to_one",
            reason="Deployment is scaled to zero and model marked a bounded auto-fix as safe.",
            command=["kubectl", "scale", "deployment", deployment_name, "-n", namespace, "--replicas=1"],
        )

    if local_class in {"crash_looping_pods", "deployment_rollout_stuck", "pods_not_ready_no_endpoints"}:
        return RemediationAction(
            action="rollout_restart_deployment",
            reason="Deployment-level restart is a bounded remediation for unhealthy pods or stuck rollout.",
            command=["kubectl", "rollout", "restart", "deployment", deployment_name, "-n", namespace],
        )

    return None


def apply_remediation(action: RemediationAction, dry_run: bool) -> Dict[str, Any]:
    rendered = " ".join(shlex.quote(part) for part in action.command)
    if dry_run:
        return {
            "executed": False,
            "dry_run": True,
            "action": action.action,
            "reason": action.reason,
            "command": rendered,
        }
    result = run_command(action.command, check=True, timeout=60)
    return {
        "executed": True,
        "dry_run": False,
        "action": action.action,
        "reason": action.reason,
        "command": rendered,
        "stdout": result.stdout,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Kubernetes outage RCA and guarded auto-fix with Bedrock.")
    parser.add_argument("--url", required=True, help="Application URL to probe.")
    parser.add_argument("--namespace", help="Optional namespace hint.")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"), help="AWS region for Bedrock.")
    parser.add_argument("--model-id", default=os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID), help="Bedrock model ID.")
    parser.add_argument(
        "--apply-fix",
        action="store_true",
        help="Apply the bounded remediation if the local heuristic and Bedrock both approve it.",
    )
    parser.add_argument(
        "--allow-scale-from-zero",
        action="store_true",
        help="Permit scaling a deployment from 0 to 1 when both heuristic and model approve it.",
    )
    parser.add_argument(
        "--output",
        choices=["json", "pretty"],
        default="pretty",
        help="Output format.",
    )
    args = parser.parse_args()

    try:
        run_kubectl(["version", "--client"], timeout=15)
        context = collect_cluster_context(args.url, args.namespace)
        local_class, local_reason = infer_local_incident_class(context)
        prompt = build_bedrock_prompt(context, local_class, local_reason)
        bedrock_assessment = invoke_bedrock(args.model_id, args.region, prompt)
        remediation = choose_remediation(
            context,
            local_class,
            bedrock_assessment,
            allow_scale_from_zero=args.allow_scale_from_zero,
        )
        remediation_result = None
        if remediation and args.apply_fix:
            remediation_result = apply_remediation(remediation, dry_run=False)
        elif remediation:
            remediation_result = apply_remediation(remediation, dry_run=True)

        output = {
            "target_url": args.url,
            "probe": context.get("probe"),
            "scope": {
                "namespace": context.get("namespace"),
                "service": (context.get("service") or {}).get("name"),
                "deployment": (context.get("deployment") or {}).get("name"),
            },
            "heuristic_assessment": {
                "incident_class": local_class,
                "reason": local_reason,
            },
            "bedrock_assessment": bedrock_assessment,
            "remediation": remediation_result,
            "evidence": context,
        }

        if args.output == "json":
            print(json.dumps(output, indent=2))
        else:
            print(f"URL: {args.url}")
            print(
                f"Probe: ok={context.get('probe', {}).get('ok')} "
                f"status={context.get('probe', {}).get('status_code')} "
                f"latency_ms={context.get('probe', {}).get('latency_ms')}"
            )
            print(f"Heuristic RCA: {local_class} - {local_reason}")
            print(
                f"Bedrock RCA: {bedrock_assessment.get('incident_class')} "
                f"(confidence={bedrock_assessment.get('confidence')})"
            )
            print(f"Root cause: {bedrock_assessment.get('root_cause')}")
            print(f"Blast radius: {bedrock_assessment.get('blast_radius')}")
            print("Recommended actions:")
            for action in bedrock_assessment.get("recommended_actions", []):
                print(f"  - {action}")
            if remediation_result:
                mode = "executed" if remediation_result.get("executed") else "planned"
                print(f"Remediation {mode}: {remediation_result.get('action')}")
                print(f"Reason: {remediation_result.get('reason')}")
                print(f"Command: {remediation_result.get('command')}")
            else:
                print("Remediation: no safe automatic action selected.")
        return 0
    except Exception as exc:  # noqa: BLE001
        error = {"error": str(exc)}
        if "--output" in sys.argv and "json" in sys.argv:
            print(json.dumps(error, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
