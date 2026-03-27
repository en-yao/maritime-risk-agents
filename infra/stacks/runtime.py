from aws_cdk import (
    CfnOutput,
    CfnParameter,
    Duration,
    Stack,
    aws_bedrock as bedrock,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cw,
    aws_codebuild as codebuild,
    aws_cognito as cognito,
    aws_iam as iam,
)
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

        # --- Parameters ---
        agentcore_endpoint = CfnParameter(
            self,
            "AgentCoreEndpoint",
            default="bedrock-agentcore.ap-southeast-1.amazonaws.com",
            description="AgentCore Runtime HTTPS endpoint hostname",
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

        # --- Cognito (JWT auth for browser → AgentCore) ---
        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name="maritime-risk-users",
            self_sign_up_enabled=True,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(min_length=8),
        )

        user_pool_client = user_pool.add_client(
            "WebClient",
            user_pool_client_name="maritime-risk-web",
            auth_flows=cognito.AuthFlow(user_srp=True),
        )

        # --- CloudFront (CORS proxy → AgentCore) ---
        distribution = cf.Distribution(
            self,
            "AgentCoreDistribution",
            default_behavior=cf.BehaviorOptions(
                origin=origins.HttpOrigin(
                    agentcore_endpoint.value_as_string,
                    protocol_policy=cf.OriginProtocolPolicy.HTTPS_ONLY,
                ),
                allowed_methods=cf.AllowedMethods.ALLOW_ALL,
                viewer_protocol_policy=cf.ViewerProtocolPolicy.HTTPS_ONLY,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cf.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                response_headers_policy=(
                    cf.ResponseHeadersPolicy.CORS_ALLOW_ALL_ORIGINS_WITH_PREFLIGHT
                ),
            ),
            comment="Maritime Risk AgentCore AG-UI proxy",
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

        cf_requests = cw.Metric(
            namespace="AWS/CloudFront",
            metric_name="Requests",
            dimensions_map={
                "DistributionId": distribution.distribution_id,
                "Region": "Global",
            },
            period=Duration.minutes(5),
            statistic="Sum",
        )
        cf_4xx = cw.Metric(
            namespace="AWS/CloudFront",
            metric_name="4xxErrorRate",
            dimensions_map={
                "DistributionId": distribution.distribution_id,
                "Region": "Global",
            },
            period=Duration.minutes(5),
            statistic="Average",
        )
        cf_5xx = cw.Metric(
            namespace="AWS/CloudFront",
            metric_name="5xxErrorRate",
            dimensions_map={
                "DistributionId": distribution.distribution_id,
                "Region": "Global",
            },
            period=Duration.minutes(5),
            statistic="Average",
        )

        dashboard.add_widgets(
            cw.Row(
                cw.GraphWidget(
                    title="CloudFront Requests",
                    left=[cf_requests],
                    width=8,
                ),
                cw.GraphWidget(
                    title="CloudFront 4xx Error Rate",
                    left=[cf_4xx],
                    width=8,
                ),
                cw.GraphWidget(
                    title="CloudFront 5xx Error Rate",
                    left=[cf_5xx],
                    width=8,
                ),
            ),
        )

        # --- Outputs ---
        CfnOutput(self, "CloudFrontUrl", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
        CfnOutput(
            self,
            "DashboardUrl",
            value=f"https://console.aws.amazon.com/cloudwatch/home"
            f"?region={self.region}#dashboards:name=maritime-risk-agents",
        )
