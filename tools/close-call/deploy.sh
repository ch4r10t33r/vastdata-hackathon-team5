#!/bin/bash
# Deploy Close Call on the lab cluster. Run this on the lab VM.
set -euo pipefail

mapfile -t TEAM_CONFIGS < <(find /config -maxdepth 1 -type f -name '*.config' | sort)
(( ${#TEAM_CONFIGS[@]} == 1 )) || { echo "expected exactly one /config/*.config" >&2; exit 1; }
TEAM_CONFIG="${TEAM_CONFIGS[0]}"

read_key() {
  grep "^${1}=" "$TEAM_CONFIG" | cut -d= -f2- | tr -d '"' | tr -d "'"
}

NS="$(read_key USERNAME)"
INGRESS_URL="$(read_key INGRESS_URL)"
PASSWORD="$(read_key PASSWORD)"
WANDB_API_KEY="${WANDB_API_KEY:-$(read_key WANDB_API_KEY || true)}"
WANDB_TEAM="${WANDB_TEAM:-$(read_key WANDB_TEAM || true)}"
WANDB_PROJECT="${WANDB_PROJECT:-$(read_key WANDB_PROJECT || true)}"

if [[ -f "/config/${NS}-k8s.yaml" ]]; then
  export KUBECONFIG="/config/${NS}-k8s.yaml"
elif [[ -f /config/kubeconfig ]]; then
  export KUBECONFIG=/config/kubeconfig
else
  echo "no kubeconfig for ${NS}" >&2
  exit 1
fi

TEAM_N="${NS#team-}"
APP_HOST="video-lab-team-${TEAM_N}.cosmos.vastdata.com"
APP_NAME=close-call
APP_PORT=8080
APP_DIR="$(cd "$(dirname "$0")" && pwd)"

kubectl -n "$NS" create configmap "${APP_NAME}-code" \
  --from-file="$APP_DIR/main.py" \
  --from-file="$APP_DIR/index.html" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NS" create secret generic "${APP_NAME}-vss-creds" \
  --from-literal=VSS_URL="$INGRESS_URL" \
  --from-literal=VSS_USERNAME="$NS" \
  --from-literal=VSS_PASSWORD="$PASSWORD" \
  --from-literal=WANDB_API_KEY="$WANDB_API_KEY" \
  --from-literal=WANDB_TEAM="$WANDB_TEAM" \
  --from-literal=WANDB_PROJECT="$WANDB_PROJECT" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    metadata:
      labels:
        app: ${APP_NAME}
    spec:
      containers:
      - name: app
        image: python:3.12-slim
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: ${APP_PORT}
        env:
        - name: PORT
          value: "${APP_PORT}"
        - name: VSS_URL
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_URL
        - name: VSS_USERNAME
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_USERNAME
        - name: VSS_PASSWORD
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_PASSWORD
        - name: WANDB_API_KEY
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: WANDB_API_KEY
        - name: WANDB_TEAM
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: WANDB_TEAM
        - name: WANDB_PROJECT
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: WANDB_PROJECT
        volumeMounts:
        - name: code
          mountPath: /code
        workingDir: /code
        command: ["python", "main.py"]
        readinessProbe:
          httpGet:
            path: /health
            port: ${APP_PORT}
          initialDelaySeconds: 3
          periodSeconds: 10
      volumes:
      - name: code
        configMap:
          name: ${APP_NAME}-code
---
apiVersion: v1
kind: Service
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  selector:
    app: ${APP_NAME}
  ports:
  - name: http
    port: 80
    targetPort: ${APP_PORT}
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /\$2
    nginx.ingress.kubernetes.io/proxy-body-size: "8m"
spec:
  ingressClassName: nginx
  rules:
  - host: ${APP_HOST}
    http:
      paths:
      - path: /app(/|$)(.*)
        pathType: ImplementationSpecific
        backend:
          service:
            name: ${APP_NAME}
            port:
              number: 80
EOF

kubectl -n "$NS" rollout restart "deploy/${APP_NAME}"
kubectl -n "$NS" rollout status "deploy/${APP_NAME}" --timeout=180s
echo "Open https://workshop.thecosmoslabs.com and click App."
