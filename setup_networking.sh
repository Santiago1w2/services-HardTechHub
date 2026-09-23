#!/bin/bash
set -e

REGION="us-east-1"
echo "=== INICIANDO CONFIGURACIÓN DE RED AUTOMATIZADA ==="

# 1. Obtener VPC y Subnets
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text --region $REGION)
SUBNETS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --query "Subnets[0:2].SubnetId" --output text --region $REGION)
SUBNET_1=$(echo$SUBNETS | awk '{print $1}')
SUBNET_2=$(echo$SUBNETS | awk '{print $2}')

# 2. Obtener y Validar Security Group (Corrección 3)
SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=SG-PROD" --query "SecurityGroups[0].GroupId" --output text --region $REGION)
if [ "$SG_ID" = "None" ] \vert{}\vert{} [ -z "$SG_ID" ]; then
    echo "❌ Error: No existe el Security Group SG-PROD."
    exit 1
fi

# 3. Obtener y Validar Instancias EC2 (Corrección 4)
PROD1_ID=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=MV PROD1" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].InstanceId" --output text --region $REGION)
PROD2_ID=$(aws ec2 describe-instances --filters "Name=tag:Name,Values=MV PROD2" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].InstanceId" --output text --region $REGION)

if [ "$PROD1_ID" = "None" ] \vert{}\vert{} [ -z "$PROD1_ID" ]; then
    echo "❌ Error: No se encontró la instancia MV PROD1 en estado running."
    exit 1
fi
if [ "$PROD2_ID" = "None" ] \vert{}\vert{} [ -z "$PROD2_ID" ]; then
    echo "❌ Error: No se encontró la instancia MV PROD2 en estado running."
    exit 1
fi

echo "-> Infraestructura base validada: VPC ($VPC_ID), SG ($SG_ID), PROD1 ($PROD1_ID), PROD2 ($PROD2_ID)."

# 4. Target Groups
echo "=== CONFIGURANDO TARGET GROUPS ==="
declare -A SERVICES=(
  ["catalog"]="8002"
  ["order"]="8003"
  ["compatibility"]="8004"
  ["analytics"]="8005"
  ["inventory"]="8006"
)

declare -A TG_ARNS

for SVC in "${!SERVICES[@]}"; do
  PORT=${SERVICES[$SVC]}
  TG_NAME="tg-${SVC}-service"
  
  echo "-> Configurando $TG_NAME en puerto$PORT..."
  TG_ARN=$(aws elbv2 create-target-group \
    --name "$TG_NAME" \
    --protocol HTTP \
    --port $PORT \
    --vpc-id $VPC_ID \
    --health-check-path "/health" \
    --target-type instance \
    --region $REGION \
    --query "TargetGroups[0].TargetGroupArn" --output text 2>/dev/null || \
    aws elbv2 describe-target-groups --names "$TG_NAME" --query "TargetGroups[0].TargetGroupArn" --output text --region $REGION)
  
  TG_ARNS[$SVC]=$TG_ARN
  aws elbv2 register-targets --target-group-arn $TG_ARN --targets Id=$PROD1_ID Id=$PROD2_ID --region$REGION >/dev/null
done

# 5. Application Load Balancer
echo "=== CONFIGURANDO LOAD BALANCER INTERNO ==="
ALB_ARN=$(aws elbv2 create-load-balancer \
  --name "alb-hardtech-internal" \
  --subnets $SUBNET_1 $SUBNET_2 \   --security-groups$SG_ID \
  --scheme internal \
  --type application \
  --region $REGION \
  --query "LoadBalancers[0].LoadBalancerArn" --output text 2>/dev/null || \
  aws elbv2 describe-load-balancers --names "alb-hardtech-internal" --query "LoadBalancers[0].LoadBalancerArn" --output text --region $REGION)

echo "Esperando que el ALB esté disponible..."
aws elbv2 wait load-balancer-available --load-balancer-arns $ALB_ARN --region$REGION

# Listener HTTP:80 con filtro seguro (Corrección 2)
LISTENER_ARN=$(aws elbv2 create-listener \
  --load-balancer-arn $ALB_ARN \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=${TG_ARNS["catalog"]} \
  --region $REGION \
  --query "Listeners[0].ListenerArn" --output text 2>/dev/null || \
  aws elbv2 describe-listeners --load-balancer-arn $ALB_ARN --query "Listeners[?Port==\`80\`].ListenerArn | [0]" --output text --region $REGION)

# 6. Reglas de Enrutamiento (Priority order)
echo "=== CONFIGURANDO REGLAS PATH-BASED ==="
aws elbv2 create-rule --listener-arn $LISTENER_ARN --priority 10 \
  --conditions Field=path-pattern,Values='/api/orders*' \
  --actions Type=forward,TargetGroupArn=${TG_ARNS["order"]} --region $REGION 2>/dev/null || true

aws elbv2 create-rule --listener-arn $LISTENER_ARN --priority 20 \
  --conditions Field=path-pattern,Values='/api/products*','/api/categories*','/api/brands*' \
  --actions Type=forward,TargetGroupArn=${TG_ARNS["catalog"]} --region $REGION 2>/dev/null || true

aws elbv2 create-rule --listener-arn $LISTENER_ARN --priority 30 \
  --conditions Field=path-pattern,Values='/api/analytics*' \
  --actions Type=forward,TargetGroupArn=${TG_ARNS["analytics"]} --region $REGION 2>/dev/null || true

aws elbv2 create-rule --listener-arn $LISTENER_ARN --priority 40 \
  --conditions Field=path-pattern,Values='/inventory*' \
  --actions Type=forward,TargetGroupArn=${TG_ARNS["inventory"]} --region $REGION 2>/dev/null || true

aws elbv2 create-rule --listener-arn $LISTENER_ARN --priority 50 \
  --conditions Field=path-pattern,Values='/api/v1/compatibility*' \
  --actions Type=forward,TargetGroupArn=${TG_ARNS["compatibility"]} --region $REGION 2>/dev/null || true

# 7. VPC Link
echo "=== CONFIGURANDO VPC LINK ==="
VPC_LINK_ID=$(aws apigatewayv2 create-vpc-link \
  --name "vpclink-hardtech" \
  --subnet-ids $SUBNET_1 $SUBNET_2 \   --security-group-ids$SG_ID \
  --region $REGION \
  --query "VpcLinkId" --output text 2>/dev/null || \
  aws apigatewayv2 get-vpc-links --region $REGION --query "Items[?Name=='vpclink-hardtech'].VpcLinkId | [0]" --output text)

echo "Esperando estado AVAILABLE en VPC Link..."
while true; do
  STATUS=$(aws apigatewayv2 get-vpc-link --vpc-link-id $VPC_LINK_ID --region$REGION --query "VpcLinkStatus" --output text)
  if [ "$STATUS" == "AVAILABLE" ]; then break; fi
  echo "Estado: $STATUS. Esperando 10s..."
  sleep 10
done

# 8. API Gateway e Integración Idempotente (Correcciones 6 y 7)
echo "=== CONFIGURANDO API GATEWAY ==="
API_ID=$(aws apigatewayv2 create-api \
  --name "HardTechHub-Gateway" \
  --protocol-type HTTP \
  --region $REGION \
  --query "ApiId" --output text 2>/dev/null || \
  aws apigatewayv2 get-apis --region $REGION --query "Items[?Name=='HardTechHub-Gateway'].ApiId | [0]" --output text)

# Verificar integración existente o crear nueva usando el LISTENER_ARN
INTEGRATION_ID=$(aws apigatewayv2 get-integrations \
  --api-id $API_ID \
  --region $REGION \
  --query "Items[?ConnectionId=='$VPC_LINK_ID'].IntegrationId | [0]" \
  --output text)

if [ "$INTEGRATION_ID" = "None" ] \vert{}\vert{} [ -z "$INTEGRATION_ID" ]; then
  INTEGRATION_ID=$(aws apigatewayv2 create-integration \
    --api-id $API_ID \
    --integration-type HTTP_PROXY \
    --integration-method ANY \
    --connection-type VPC_LINK \
    --connection-id $VPC_LINK_ID \
    --integration-uri $LISTENER_ARN \
    --payload-format-version "1.0" \
    --region $REGION \
    --query "IntegrationId" \
    --output text)
fi

aws apigatewayv2 create-route \
  --api-id $API_ID \
  --route-key 'ANY /{proxy+}' \
  --target "integrations/$INTEGRATION_ID" \
  --region $REGION 2>/dev/null || true

# Asegurar stage $default
aws apigatewayv2 get-stage --api-id $API_ID --stage-name '$default' --region$REGION >/dev/null 2>&1 || \
aws apigatewayv2 create-stage \
  --api-id $API_ID \
  --stage-name '$default' \
  --auto-deploy \
  --region $REGION

INVOKE_URL=$(aws apigatewayv2 get-api --api-id $API_ID --region$REGION --query "ApiEndpoint" --output text)

echo "============================================================"
echo "✅ DESPLIEGUE DE RED COMPLETO"
echo "🌐 URL DEL API GATEWAY (HTTPS): $INVOKE_URL"
echo "============================================================"
