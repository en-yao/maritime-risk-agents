"""Lambda proxy: API Gateway → AgentCore Runtime."""
import json
import os
import uuid

import boto3

client = boto3.client("bedrock-agentcore", region_name=os.environ["AWS_REGION"])
AGENT_ARN = os.environ["AGENT_ARN"]


def handler(event, context):
    # Parse body from API Gateway
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON"})}

    prompt = payload.get("prompt", "")
    if not prompt:
        return {"statusCode": 400, "body": json.dumps({"error": "Missing prompt"})}

    response = client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_ARN,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        runtimeSessionId=str(uuid.uuid4()),
        contentType="application/json",
        accept="application/json",
    )

    result = response["response"].read().decode("utf-8")

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        },
        "body": result,
    }
