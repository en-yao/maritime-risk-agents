from aws_cdk import (
    CfnOutput,
    Stack,
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
                    secrets.anthropic_api_key.secret_arn,
                ],
            )
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

        # --- Outputs ---
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
