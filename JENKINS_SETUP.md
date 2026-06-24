# Jenkins Configuration Guide

## Image Tag Setup with .env File

This project uses a `.env` configuration file to manage build parameters. Here's how it works with Jenkins:

### Understanding the Setup

1. **Local Development**: `.env` exists in your local workspace
2. **Jenkins Pipeline**: The `.env` file is NOT in Git (it's in `.gitignore` for security)
3. **Jenkins Behavior**: 
   - If `.env` exists → Load configuration from it
   - If `.env` doesn't exist → Use Jenkins build parameters as fallback

### Image Tag Format

```
REGISTRY/IMAGE_REPOSITORY:BUILD_NUMBER-GIT_SHA

Example: docker.io/kawalepankaj/python-app:123-abc1234
```

Where:
- `BUILD_NUMBER`: Jenkins build number (auto-incremented)
- `GIT_SHA`: First 7 characters of Git commit hash

### Two Approaches to Configure Jenkins

#### Option 1: Using Jenkins Build Parameters (RECOMMENDED)

Jenkins automatically uses these parameters if `.env` is not found:

```groovy
parameters {
  string(name: 'REGISTRY', defaultValue: 'docker.io', description: 'Container registry')
  string(name: 'IMAGE_REPOSITORY', defaultValue: 'kawalepankaj/python-app', description: 'Repository')
  string(name: 'KUBE_NAMESPACE', defaultValue: 'production', description: 'K8s namespace')
}
```

**How to use:**
1. Go to Jenkins job → **Build with Parameters**
2. Update values if needed
3. Click **Build**

#### Option 2: Using .env File in Jenkins (OPTIONAL)

If you want to use `.env` file in Jenkins:

1. Create `.env` in Jenkins workspace:
   ```bash
   # SSH into Jenkins server
   cd /var/lib/jenkins/workspace/python-app
   cat > .env << 'EOF'
   REGISTRY=docker.io
   IMAGE_REPOSITORY=kawalepankaj/python-app
   KUBE_NAMESPACE=production
   EOF
   ```

2. Or create it as a Jenkins Credential (Secret file):
   - Go to **Manage Jenkins** → **Manage Credentials**
   - Add `.env` as a "Secret file" credential
   - Modify Jenkinsfile to copy it before pipeline runs

### Step-by-Step Setup

#### Step 1: Verify Jenkinsfile Configuration
The current Jenkinsfile automatically:
- ✅ Checks if `.env` exists
- ✅ Falls back to parameters if not found
- ✅ Constructs image tag as `BUILD_NUMBER-GIT_SHA`
- ✅ Updates deployment dynamically

#### Step 2: Configure Jenkins Job
1. Create a new **Pipeline** job
2. Point to your Git repository
3. Set up credentials for:
   - Git repository access
   - Docker registry (for push)
   - Kubernetes kubeconfig (for deployment)

#### Step 3: Set Jenkins Credentials

**Docker Registry Credentials:**
- Jenkins: **Manage Jenkins** → **Manage Credentials**
- Add: **Username with password** credential
- ID: `docker-registry-creds` (must match Jenkinsfile)

**Kubernetes Config:**
- Add: **Secret file** credential
- ID: `kubeconfig-prod` (must match Jenkinsfile)
- Upload your kubeconfig file

**Git Repository:**
- Add: **SSH Key** or **Username with password**
- ID: `git_cred` (used in Jenkinsfile)

#### Step 4: Run Pipeline
1. Click **Build with Parameters** in Jenkins job
2. Leave defaults or customize:
   - REGISTRY: `docker.io`
   - IMAGE_REPOSITORY: `kawalepankaj/python-app`
   - KUBE_NAMESPACE: `production`
3. Click **Build**

### Pipeline Flow

```
Load Configuration
    ↓
Checkout (from Git)
    ↓
Compute: IMAGE_TAG = BUILD_NUMBER-GIT_SHA
    ↓
Test (run pytest)
    ↓
Build Image (docker build -t $IMAGE_TAG)
    ↓
Scan Image (trivy scan)
    ↓
Push Image (docker push $IMAGE_TAG)
    ↓
Deploy (kubectl apply)
    ↓
Update Deployment (kubectl set image)
    ↓
Wait for Rollout
```

### Troubleshooting

#### Error: `.env` file not found
✅ **Solution**: Use Jenkins build parameters (this is now the fallback)

#### Docker push fails
- Check `docker-registry-creds` credential ID matches Jenkinsfile
- Verify credentials are correct in Jenkins

#### kubectl fails
- Check `kubeconfig-prod` credential ID is correct
- Ensure kubeconfig has access to the target cluster

#### Image tag not updating
- Verify `kubectl set image` command in Deploy stage
- Check deployment name: `sample-fastapi-app`
- Verify container name: `app`

### Local Development

To test locally without Jenkins:

```bash
# Set environment variables
export REGISTRY=docker.io
export IMAGE_REPOSITORY=kawalepankaj/python-app
export BUILD_NUMBER=123
export GIT_SHA=$(git rev-parse --short=7 HEAD)

# Build image with computed tag
docker build -t $REGISTRY/$IMAGE_REPOSITORY:$BUILD_NUMBER-$GIT_SHA app/

# Test tag format
echo $REGISTRY/$IMAGE_REPOSITORY:$BUILD_NUMBER-$GIT_SHA
# Output: docker.io/kawalepankaj/python-app:123-abc1234
```

### Important Notes

1. **Never commit `.env` to Git** (already in .gitignore)
2. **Use Jenkins Credentials Manager** for sensitive data (passwords, tokens)
3. **Image tags are computed at runtime** - no manual updates needed
4. **Rollback**: Use `kubectl rollout undo` to revert to previous image

