from aws_cdk import Stack, aws_secretsmanager as sm
from constructs import Construct


class SecretsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.noaa_token = sm.Secret(
            self,
            "NoaaToken",
            secret_name="maritime-risk/noaa-token",
            description="NOAA CDO API token",
        )

        self.dd_api_key = sm.Secret(
            self,
            "DatadogApiKey",
            secret_name="maritime-risk/dd-api-key",
            description="Datadog API key",
        )

        self.marinetraffic_api_key = sm.Secret(
            self,
            "MarineTrafficApiKey",
            secret_name="maritime-risk/marinetraffic-api-key",
            description="MarineTraffic Essential API key",
        )

        self.anthropic_api_key = sm.Secret(
            self,
            "AnthropicApiKey",
            secret_name="maritime-risk/anthropic-api-key",
            description="Anthropic API key for Claude model access",
        )
