from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_bedrock as bedrock,
    aws_cloudwatch as cw,
    aws_codebuild as codebuild,
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

        # --- CI/CD (CodeBuild + GitHub) ---
        build_project = codebuild.Project(
            self,
            "CIBuild",
            project_name="maritime-risk-agents",
            source=codebuild.Source.git_hub(
                owner="en-yao",
                repo="maritime-risk-agents",
                webhook=True,
                webhook_filters=[
                    codebuild.FilterGroup.in_event_of(
                        codebuild.EventAction.PUSH,
                    ).and_branch_is("main"),
                ],
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
            ),
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"),
        )

        # Grant CodeBuild permission to deploy CDK stacks
        build_project.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
            )
        )

        # --- CloudWatch Dashboard ---
        dashboard = cw.Dashboard(
            self,
            "AgentDashboard",
            dashboard_name="maritime-risk-agents",
        )

        # Lambda proxy metrics
        dashboard.add_widgets(
            cw.Row(
                cw.GraphWidget(
                    title="Lambda Invocations",
                    left=[proxy_fn.metric_invocations(period=Duration.minutes(5))],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Lambda Errors",
                    left=[proxy_fn.metric_errors(period=Duration.minutes(5))],
                    width=8,
                ),
                cw.GraphWidget(
                    title="Lambda Duration (p50 / p95)",
                    left=[
                        proxy_fn.metric_duration(
                            statistic="p50", period=Duration.minutes(5),
                        ),
                        proxy_fn.metric_duration(
                            statistic="p95", period=Duration.minutes(5),
                        ),
                    ],
                    width=8,
                ),
            ),
        )

        # API Gateway metrics
        api_id = api.http_api_id
        api_4xx = cw.Metric(
            namespace="AWS/ApiGateway",
            metric_name="4xx",
            dimensions_map={"ApiId": api_id},
            period=Duration.minutes(5),
            statistic="Sum",
        )
        api_5xx = cw.Metric(
            namespace="AWS/ApiGateway",
            metric_name="5xx",
            dimensions_map={"ApiId": api_id},
            period=Duration.minutes(5),
            statistic="Sum",
        )
        api_latency = cw.Metric(
            namespace="AWS/ApiGateway",
            metric_name="Latency",
            dimensions_map={"ApiId": api_id},
            period=Duration.minutes(5),
            statistic="p95",
        )

        dashboard.add_widgets(
            cw.Row(
                cw.GraphWidget(
                    title="API Gateway 4xx / 5xx",
                    left=[api_4xx],
                    right=[api_5xx],
                    width=12,
                ),
                cw.GraphWidget(
                    title="API Gateway Latency (p95)",
                    left=[api_latency],
                    width=12,
                ),
            ),
        )

        # --- Outputs ---
        CfnOutput(self, "ApiUrl", value=api.url or "")
        CfnOutput(
            self,
            "ProxyFunctionName",
            value=proxy_fn.function_name,
            description="Update AGENT_ARN env var after agentcore launch",
        )
        CfnOutput(
            self,
            "DashboardUrl",
            value=f"https://console.aws.amazon.com/cloudwatch/home"
            f"?region={self.region}#dashboards:name=maritime-risk-agents",
        )
