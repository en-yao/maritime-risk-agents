from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_bedrock as bedrock,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_lambda as lambda_,
)
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct

from stacks.secrets import SecretsStack


class RuntimeStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        secrets: SecretsStack,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- VPC ---
        vpc = ec2.Vpc(
            self,
            "MaritimeRiskVpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # --- Security Group ---
        ec2.SecurityGroup(
            self,
            "AgentSecurityGroup",
            vpc=vpc,
            description="Security group for maritime risk agents",
            allow_all_outbound=True,
        )

        # --- IAM Role (Bedrock) ---
        agent_role = iam.Role(
            self,
            "AgentRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="IAM role for maritime risk agents",
        )

        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=[
                    f"arn:aws:bedrock:*:{self.account}:inference-profile/us.*",
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                ],
            )
        )

        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    secrets.noaa_token.secret_arn,
                    secrets.dd_api_key.secret_arn,
                    secrets.anthropic_api_key.secret_arn,
                ],
            )
        )

        # --- Guardrail ---
        bedrock.CfnGuardrail(
            self,
            "ContentSafetyGuardrail",
            name="maritime-risk-content-safety",
            blocked_input_messaging="Request blocked by content safety guardrail.",
            blocked_outputs_messaging="Response blocked by content safety guardrail.",
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="SEXUAL",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="HATE",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="VIOLENCE",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="INSULTS",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="MISCONDUCT",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK",
                        input_strength="HIGH",
                        output_strength="NONE",
                    ),
                ],
            ),
        )

        # --- Lambda Proxy (API Gateway → AgentCore Runtime) ---
        proxy_fn = lambda_.Function(
            self,
            "AgentCoreProxy",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="proxy.handler",
            code=lambda_.Code.from_asset("../infra/lambda"),
            timeout=Duration.seconds(300),
            memory_size=256,
            environment={
                "AGENT_ARN": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}"
                ":runtime/PLACEHOLDER",
            },
        )

        proxy_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[
                    f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/*",
                ],
            )
        )

        # --- API Gateway HTTP API ---
        api = apigwv2.HttpApi(
            self,
            "AgentApi",
            api_name="maritime-risk-api",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.OPTIONS],
                allow_headers=["Content-Type"],
            ),
        )

        integration = HttpLambdaIntegration("ProxyIntegration", proxy_fn)

        api.add_routes(
            path="/invocations",
            methods=[apigwv2.HttpMethod.POST],
            integration=integration,
        )

        # --- Outputs ---
        CfnOutput(self, "ApiUrl", value=api.url or "")
        CfnOutput(
            self,
            "ProxyFunctionName",
            value=proxy_fn.function_name,
            description="Update AGENT_ARN env var after agentcore launch",
        )
