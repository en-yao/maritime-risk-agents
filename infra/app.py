#!/usr/bin/env python3
import aws_cdk as cdk

from stacks.runtime import RuntimeStack
from stacks.secrets import SecretsStack

app = cdk.App()

env = cdk.Environment(region="ap-southeast-1")

secrets = SecretsStack(app, "MaritimeRiskSecrets", env=env)
RuntimeStack(app, "MaritimeRiskRuntime", secrets=secrets, env=env)

app.synth()
