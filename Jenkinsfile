pipeline {
  agent any

  options {
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
    REGISTRY = 'docker.io'
    IMAGE_REPOSITORY = 'kawalepankaj/python-app'
    KUBE_NAMESPACE = 'production'
  }

  stages {
    stage('Load Configuration') {
      steps {
        script {
          // Try to load configuration from .env file (if it exists)
          if (fileExists('.env')) {
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
            echo "✓ Configuration loaded from .env file"
          } else {
            // Fallback to parameters if .env doesn't exist
            env.REGISTRY = params.REGISTRY
            env.IMAGE_REPOSITORY = params.IMAGE_REPOSITORY
            env.KUBE_NAMESPACE = params.KUBE_NAMESPACE
            echo "⚠ .env file not found, using default parameters"
          }
        }
      }
    }

    stage('Checkout') {
      steps {
        script {
          // Fix permissions on any files left from previous builds (especially from Docker root user)
          sh '''
            # Force remove problematic cache directories
            rm -rf .git .pytest_cache __pycache__ app/tests/__pycache__ app/.pytest_cache 2>/dev/null || true
            # Fix permissions on remaining files
            find . -type d -exec chmod 777 {} + 2>/dev/null || true
            find . -type f -exec chmod 666 {} + 2>/dev/null || true
          '''
        }
        git url: 'https://github.com/kawalepankaj/python-app.git', branch: 'master', credentialsId: 'git_cred'
        // Capture Git SHA to file for cross-stage persistence
        sh '''
          GIT_SHA=$(git rev-parse --short=7 HEAD)
          echo "Git SHA: $GIT_SHA"
          echo "$GIT_SHA" > .build_info
          chmod 644 .build_info
        '''
      }
    }

    stage('Test') {
      agent {
        docker {
          image 'python:3.12-slim'
          args '-v /var/run/docker.sock:/var/run/docker.sock -u root'
          reuseNode true
        }
      }
      steps {
        dir(env.APP_DIR) {
          sh '''
            pip install --upgrade pip --root-user-action=ignore
            pip install -r requirements-dev.txt --root-user-action=ignore
            ruff check .
            PYTHONPATH=. pytest -q
          '''
        }
      }
    }

    stage('Build Image') {
      steps {
        sh '''
          # Read Git SHA from file written in Checkout stage
          if [ -f .build_info ]; then
            GIT_SHA=$(cat .build_info)
          else
            echo "ERROR: .build_info file not found!"
            exit 1
          fi
          
          # Construct image tags
          IMAGE="${REGISTRY}/${IMAGE_REPOSITORY}:${BUILD_NUMBER}-${GIT_SHA}"
          LATEST_IMAGE="${REGISTRY}/${IMAGE_REPOSITORY}:latest"
          
          echo "========== Build Configuration =========="
          echo "REGISTRY: $REGISTRY"
          echo "IMAGE_REPOSITORY: $IMAGE_REPOSITORY"
          echo "BUILD_NUMBER: $BUILD_NUMBER"
          echo "GIT_SHA: $GIT_SHA"
          echo "IMAGE: $IMAGE"
          echo "LATEST_IMAGE: $LATEST_IMAGE"
          echo "APP_DIR: $APP_DIR"
          echo "=========================================="
          
          # Validate tags before building
          if [ -z "$IMAGE" ] || [ -z "$LATEST_IMAGE" ]; then
            echo "ERROR: Image tags are empty!"
            exit 1
          fi
          
          # Build image with both tags
          docker build --pull -t "$IMAGE" -t "$LATEST_IMAGE" "$APP_DIR"
        '''
      }
    }

    stage('Scan Image') {
      steps {
        sh '''
          GIT_SHA=$(cat .build_info)
          IMAGE="${REGISTRY}/${IMAGE_REPOSITORY}:${BUILD_NUMBER}-${GIT_SHA}"
          
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
            GIT_SHA=$(cat .build_info)
            IMAGE="${REGISTRY}/${IMAGE_REPOSITORY}:${BUILD_NUMBER}-${GIT_SHA}"
            LATEST_IMAGE="${REGISTRY}/${IMAGE_REPOSITORY}:latest"
            
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
            GIT_SHA=$(cat .build_info)
            IMAGE="${REGISTRY}/${IMAGE_REPOSITORY}:${BUILD_NUMBER}-${GIT_SHA}"
            
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
      sh '''
        # Fix permissions on pytest cache created by root in docker
        find . -name ".pytest_cache" -type d -exec chmod -R 777 {} + 2>/dev/null || true
        find . -name "__pycache__" -type d -exec chmod -R 777 {} + 2>/dev/null || true
        docker logout "$REGISTRY" || true
      '''
      cleanWs(deleteDirs: true)
    }
  }
}
