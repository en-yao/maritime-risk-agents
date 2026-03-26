from aws_cdk import (
    RemovalPolicy,
    Stack,
    aws_bedrock as bedrock,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
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
        agent_sg = ec2.SecurityGroup(
            self,
            "AgentSecurityGroup",
            vpc=vpc,
            description="Security group for maritime risk agents",
            allow_all_outbound=True,
        )

        # --- IAM Role ---
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
                    secrets.marinetraffic_api_key.secret_arn,
                ],
            )
        )

        # --- S3 Reference Data ---
        data_bucket = s3.Bucket(
            self,
            "ReferenceDataBucket",
            bucket_name=f"maritime-risk-data-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        s3_deploy.BucketDeployment(
            self,
            "PortDataDeployment",
            sources=[s3_deploy.Source.asset("../data")],
            destination_bucket=data_bucket,
            destination_key_prefix="ports",
        )

        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[data_bucket.arn_for_objects("*")],
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
