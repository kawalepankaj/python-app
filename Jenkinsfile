pipeline {
  agent any

  options {
    ansiColor('xterm')
    buildDiscarder(logRotator(numToKeepStr: '20'))
    disableConcurrentBuilds()
    timestamps()
    timeout(time: 30, unit: 'MINUTES')
  }

  parameters {
    string(name: 'REGISTRY', defaultValue: 'docker.io', description: 'Container registry host')
    string(name: 'IMAGE_REPOSITORY', defaultValue: 'kawalepankaj/python-app', description: 'Registry repository')
    string(name: 'KUBE_NAMESPACE', defaultValue: 'production', description: 'Kubernetes namespace')
  }

  environment {
    APP_DIR = 'app'
    IMAGE = ''
    LATEST_IMAGE = ''
    GIT_SHA = ''
  }

  stages {
    stage('Load Configuration') {
      steps {
        script {
          // Load configuration from .env file
          def envContent = readFile('.env').trim()
          envContent.split('\n').each { line ->
            if (!line.startsWith('#') && line.contains('=')) {
              def parts = line.split('=', 2)
              def key = parts[0].trim()
              def value = parts[1].trim()
              // Skip variable references, use actual values for REGISTRY and IMAGE_REPOSITORY
              if (!value.contains('$')) {
                env[key] = value
              }
            }
          }
        }
      }
    }

    stage('Checkout') {
      steps {
        git url: 'https://github.com/kawalepankaj/python-app.git', branch: 'master', credentialsId: 'git_cred'
        script {
          def shortSha = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
          env.GIT_SHA = shortSha
          env.IMAGE = "${params.REGISTRY}/${params.IMAGE_REPOSITORY}:${env.BUILD_NUMBER}-${shortSha}"
          env.LATEST_IMAGE = "${params.REGISTRY}/${params.IMAGE_REPOSITORY}:latest"
        }
      }
    }

    stage('Test') {
      steps {
        dir(env.APP_DIR) {
          sh '''
            python3 -m venv .venv
            . .venv/bin/activate
            pip install --upgrade pip
            pip install -r requirements-dev.txt
            ruff check .
            pytest -q
          '''
        }
      }
    }

    stage('Build Image') {
      steps {
        sh 'docker build --pull -t "$IMAGE" -t "$LATEST_IMAGE" "$APP_DIR"'
      }
    }

    stage('Scan Image') {
      steps {
        sh '''
          if command -v trivy >/dev/null 2>&1; then
            trivy image --exit-code 1 --severity HIGH,CRITICAL "$IMAGE"
          else
            echo "Trivy not installed on agent; skipping image scan."
          fi
        '''
      }
    }

    stage('Push Image') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'Docker_Cred', usernameVariable: 'REGISTRY_USER', passwordVariable: 'REGISTRY_PASSWORD')]) {
          sh '''
            echo "$REGISTRY_PASSWORD" | docker login "$REGISTRY" --username "$REGISTRY_USER" --password-stdin
            docker push "$IMAGE"
            docker push "$LATEST_IMAGE"
          '''
        }
      }
    }

    stage('Deploy') {
      steps {
        withCredentials([file(credentialsId: 'kubeconfig-prod', variable: 'KUBECONFIG')]) {
          sh '''
            kubectl create namespace "$KUBE_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
            kubectl -n "$KUBE_NAMESPACE" apply -f k8s/serviceaccount.yaml
            kubectl -n "$KUBE_NAMESPACE" apply -f k8s/configmap.yaml
            kubectl -n "$KUBE_NAMESPACE" apply -f k8s/service.yaml
            kubectl -n "$KUBE_NAMESPACE" apply -f k8s/deployment.yaml
            kubectl -n "$KUBE_NAMESPACE" apply -f k8s/ingress.yaml
            kubectl -n "$KUBE_NAMESPACE" set image deployment/sample-fastapi-app app="$IMAGE"
            kubectl -n "$KUBE_NAMESPACE" rollout status deployment/sample-fastapi-app --timeout=180s
          '''
        }
      }
    }
  }

  post {
    always {
      sh 'docker logout "$REGISTRY" || true'
      cleanWs(deleteDirs: true, disableDeferredWipeout: true)
    }
  }
}
