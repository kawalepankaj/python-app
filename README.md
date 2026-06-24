# Production CI/CD Assignment

This repository contains a production-ready sample application and delivery pipeline for the DevOps CI/CD assignment. It builds, tests, containerizes, publishes, deploys to Kubernetes, and exposes monitoring metrics.

## Repository And Image

- GitHub repository: [kawalepankaj/python-app](https://github.com/kawalepankaj/python-app.git)
- Docker image repository: `docker.io/kawalepankaj/python-app`

## Architecture

- Application: Python FastAPI service
- Container: Docker multi-stage image with non-root runtime user
- CI/CD: Jenkins declarative pipeline
- Registry: Docker Hub or Amazon ECR
- Deployment: Kubernetes Deployment, Service, ConfigMap, ServiceAccount, Ingress
- Monitoring: Prometheus metrics endpoint, ServiceMonitor, PrometheusRule, Grafana dashboard

## Repository Layout

```text
app/
  Dockerfile
  main.py
  requirements.txt
  requirements-dev.txt
  tests/
Jenkinsfile
k8s/
  configmap.yaml
  deployment.yaml
  ingress.yaml
  service.yaml
  serviceaccount.yaml
monitoring/
  grafana-dashboard.json
  prometheus-rule.yaml
  service-monitor.yaml
```

## Local Development

```bash
cd app
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn main:app --host 0.0.0.0 --port 3000
```

Health checks:

```bash
curl http://localhost:3000/healthz
curl http://localhost:3000/readyz
curl http://localhost:3000/metrics
```

Run tests:

```bash
cd app
pytest -q
```

## Docker Build And Run

```bash
cd app
docker build -t sample-fastapi-app:local .
docker run --rm -p 3000:3000 --env-file .env.example sample-fastapi-app:local
```

## Jenkins CI/CD Flow

The [Jenkinsfile](./Jenkinsfile) performs:

1. Checkout source from GitHub
2. Install dependencies and run tests
3. Build Docker image
4. Scan image when Trivy is installed on the Jenkins agent
5. Push image to a container registry
6. Deploy to Kubernetes using plain manifest files
7. Verify rollout status

This pipeline is intended for a Linux Jenkins agent. The agent should have `python3`, `python3-venv`, `docker`, and `kubectl` installed, and the Jenkins user must be allowed to run Docker commands.

Required Jenkins credentials:

- `docker-registry-creds`: username/password credential for Docker Hub. Use Docker Hub username `kawalepankaj` and a Docker access token as the password.
- `github-pat`: secret text credential for GitHub access if the Jenkins job is not using a GitHub App or repository webhook credential.
- `kubeconfig-prod`: secret file credential containing kubeconfig for the target cluster

Set Jenkins parameters:

- `REGISTRY`: registry host, for example `docker.io`
- `IMAGE_REPOSITORY`: image repository, for example `kawalepankaj/python-app`
- `KUBE_NAMESPACE`: deployment namespace, for example `production`

Never commit Docker or GitHub tokens to this repository. Store them in Jenkins credentials, GitHub Actions secrets, or your local credential manager.

## Kubernetes Deployment

Review and update:

- [k8s/ingress.yaml](./k8s/ingress.yaml) host name
- [k8s/configmap.yaml](./k8s/configmap.yaml) environment values
- [k8s/secret.example.yaml](./k8s/secret.example.yaml) secret values before applying

Manual deploy:

```bash
kubectl create namespace production --dry-run=client -o yaml | kubectl apply -f -
kubectl -n production apply -f k8s/serviceaccount.yaml
kubectl -n production apply -f k8s/configmap.yaml
kubectl -n production apply -f k8s/service.yaml
kubectl -n production apply -f k8s/deployment.yaml
kubectl -n production apply -f k8s/ingress.yaml
kubectl -n production rollout status deployment/sample-fastapi-app
```

## Monitoring

The application exposes Prometheus metrics at `/metrics`. If the cluster runs Prometheus Operator:

```bash
kubectl apply -f monitoring/service-monitor.yaml
kubectl apply -f monitoring/prometheus-rule.yaml
```

Import [monitoring/grafana-dashboard.json](./monitoring/grafana-dashboard.json) into Grafana.

## Assignment Deliverables

- Dockerfile: [app/Dockerfile](./app/Dockerfile)
- Jenkins pipeline: [Jenkinsfile](./Jenkinsfile)
- Deployment configuration: [k8s](./k8s)
- Monitoring configuration: [monitoring](./monitoring)
- Running application URL: set after deploying the configured Ingress host
