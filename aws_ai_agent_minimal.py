"""
AWS AI Infra Agent -- MINIMAL VERSION (OpenAI/ChatGPT)
-------------------------------------------------------
Covers ONLY the core/main resources:
  VPC, Subnet, Internet Gateway, Route Table, Security Group,
  Key Pair, EC2, S3, RDS

Removed compared to the full version: EKS, Lambda, ECR, ELB/DNS/CDN,
KMS/Secrets/Cognito/ACM, DynamoDB/Aurora/ElastiCache, SNS/SQS/EventBridge/
API Gateway/Step Functions, CloudWatch/CodeBuild/CodePipeline/CloudFormation,
Glue/Athena/Kinesis, Bedrock/SageMaker.

Setup:
    pip install boto3 openai
    aws configure
    export OPENAI_API_KEY="your-key"

Run:
    python aws_ai_agent_minimal.py
"""

import json
import time
import boto3
import openai

REGION = "ap-south-1"

ec2 = boto3.client("ec2", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
rds = boto3.client("rds", region_name=REGION)

client = openai.OpenAI()

# =====================================================================
# NETWORKING
# =====================================================================

def create_vpc(cidr_block="10.0.0.0/16", name="agent-vpc"):
    resp = ec2.create_vpc(CidrBlock=cidr_block)
    vpc_id = resp["Vpc"]["VpcId"]
    ec2.create_tags(Resources=[vpc_id], Tags=[{"Key": "Name", "Value": name}])
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    return {"vpc_id": vpc_id, "cidr": cidr_block}


def create_subnet(vpc_id, cidr_block="10.0.1.0/24", az=None, name="agent-subnet", public=True):
    kwargs = {"VpcId": vpc_id, "CidrBlock": cidr_block}
    if az:
        kwargs["AvailabilityZone"] = az
    resp = ec2.create_subnet(**kwargs)
    subnet_id = resp["Subnet"]["SubnetId"]
    ec2.create_tags(Resources=[subnet_id], Tags=[{"Key": "Name", "Value": name}])
    if public:
        ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={"Value": True})
    return {"subnet_id": subnet_id}


def create_internet_gateway(vpc_id, name="agent-igw"):
    resp = ec2.create_internet_gateway()
    igw_id = resp["InternetGateway"]["InternetGatewayId"]
    ec2.create_tags(Resources=[igw_id], Tags=[{"Key": "Name", "Value": name}])
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    return {"igw_id": igw_id}


def create_route_table(vpc_id, igw_id, subnet_ids, name="agent-rt"):
    resp = ec2.create_route_table(VpcId=vpc_id)
    rt_id = resp["RouteTable"]["RouteTableId"]
    ec2.create_tags(Resources=[rt_id], Tags=[{"Key": "Name", "Value": name}])
    ec2.create_route(RouteTableId=rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    if isinstance(subnet_ids, str):
        subnet_ids = [subnet_ids]
    for sid in subnet_ids:
        ec2.associate_route_table(RouteTableId=rt_id, SubnetId=sid)
    return {"route_table_id": rt_id}


def create_security_group(vpc_id, name="agent-sg", description="Agent created SG", ports=None):
    resp = ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)
    sg_id = resp["GroupId"]
    if ports:
        permissions = [
            {
                "IpProtocol": "tcp",
                "FromPort": p["port"],
                "ToPort": p["port"],
                "IpRanges": [{"CidrIp": p.get("cidr", "0.0.0.0/0")}],
            }
            for p in ports
        ]
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=permissions)
    return {"sg_id": sg_id}


def create_key_pair(key_name="agent-key"):
    resp = ec2.create_key_pair(KeyName=key_name)
    with open(f"{key_name}.pem", "w") as f:
        f.write(resp["KeyMaterial"])
    return {"key_name": key_name, "saved_to": f"{key_name}.pem"}


# =====================================================================
# EC2
# =====================================================================

def run_ec2_instance(ami_id, instance_type, key_name, subnet_id, sg_id, name="agent-ec2", storage_gb=8):
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=instance_type,
        KeyName=key_name,
        MaxCount=1,
        MinCount=1,
        NetworkInterfaces=[{
            "DeviceIndex": 0,
            "SubnetId": subnet_id,
            "Groups": [sg_id],
            "AssociatePublicIpAddress": True,
        }],
        BlockDeviceMappings=[{
            "DeviceName": "/dev/xvda",
            "Ebs": {"VolumeSize": storage_gb, "VolumeType": "gp3"},
        }],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": name}]}],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    return {"instance_id": instance_id}


# =====================================================================
# S3
# =====================================================================

def create_s3_bucket(bucket_name):
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    return {"bucket": bucket_name}


# =====================================================================
# RDS
# =====================================================================

def create_rds_subnet_group(name, subnet_ids, description="Agent created DB subnet group"):
    if isinstance(subnet_ids, str):
        subnet_ids = [subnet_ids]
    rds.create_db_subnet_group(
        DBSubnetGroupName=name,
        DBSubnetGroupDescription=description,
        SubnetIds=subnet_ids,
    )
    return {"subnet_group_name": name}


def create_rds_instance(db_identifier, instance_class, engine, master_username,
                         master_password, subnet_group_name, vpc_security_group_ids,
                         allocated_storage=20, engine_version=None, multi_az=False):
    kwargs = dict(
        DBInstanceIdentifier=db_identifier,
        DBInstanceClass=instance_class,
        Engine=engine,
        MasterUsername=master_username,
        MasterUserPassword=master_password,
        AllocatedStorage=allocated_storage,
        DBSubnetGroupName=subnet_group_name,
        VpcSecurityGroupIds=(
            [vpc_security_group_ids] if isinstance(vpc_security_group_ids, str) else vpc_security_group_ids
        ),
        MultiAZ=multi_az,
        PubliclyAccessible=False,
        BackupRetentionPeriod=1,
    )
    if engine_version:
        kwargs["EngineVersion"] = engine_version
    rds.create_db_instance(**kwargs)
    return {"db_identifier": db_identifier, "status": "CREATING (takes several minutes)"}


# =====================================================================
# TOOL REGISTRY
# =====================================================================

TOOL_FUNCTIONS = {
    "create_vpc": create_vpc,
    "create_subnet": create_subnet,
    "create_internet_gateway": create_internet_gateway,
    "create_route_table": create_route_table,
    "create_security_group": create_security_group,
    "create_key_pair": create_key_pair,
    "run_ec2_instance": run_ec2_instance,
    "create_s3_bucket": create_s3_bucket,
    "create_rds_subnet_group": create_rds_subnet_group,
    "create_rds_instance": create_rds_instance,
}

TOOLS = [
    {"name": "create_vpc", "description": "Create a new VPC.",
     "input_schema": {"type": "object", "properties": {
         "cidr_block": {"type": "string"}, "name": {"type": "string"}},
         "required": ["cidr_block"]}},

    {"name": "create_subnet", "description": "Create a subnet inside a VPC.",
     "input_schema": {"type": "object", "properties": {
         "vpc_id": {"type": "string"}, "cidr_block": {"type": "string"},
         "az": {"type": "string"}, "name": {"type": "string"},
         "public": {"type": "boolean"}},
         "required": ["vpc_id", "cidr_block"]}},

    {"name": "create_internet_gateway", "description": "Create and attach an IGW to a VPC.",
     "input_schema": {"type": "object", "properties": {
         "vpc_id": {"type": "string"}, "name": {"type": "string"}},
         "required": ["vpc_id"]}},

    {"name": "create_route_table", "description": "Create a route table with 0.0.0.0/0 via IGW, associate subnets.",
     "input_schema": {"type": "object", "properties": {
         "vpc_id": {"type": "string"}, "igw_id": {"type": "string"},
         "subnet_ids": {"type": "array", "items": {"type": "string"}},
         "name": {"type": "string"}},
         "required": ["vpc_id", "igw_id", "subnet_ids"]}},

    {"name": "create_security_group", "description": "Create a security group with inbound port rules.",
     "input_schema": {"type": "object", "properties": {
         "vpc_id": {"type": "string"}, "name": {"type": "string"},
         "description": {"type": "string"},
         "ports": {"type": "array", "items": {"type": "object", "properties": {
             "port": {"type": "integer"}, "cidr": {"type": "string"}}}}},
         "required": ["vpc_id"]}},

    {"name": "create_key_pair", "description": "Create an EC2 key pair, save .pem locally.",
     "input_schema": {"type": "object", "properties": {"key_name": {"type": "string"}},
         "required": ["key_name"]}},

    {"name": "run_ec2_instance", "description": "Launch an EC2 instance.",
     "input_schema": {"type": "object", "properties": {
         "ami_id": {"type": "string"}, "instance_type": {"type": "string"},
         "key_name": {"type": "string"}, "subnet_id": {"type": "string"},
         "sg_id": {"type": "string"}, "name": {"type": "string"},
         "storage_gb": {"type": "integer"}},
         "required": ["ami_id", "instance_type", "key_name", "subnet_id", "sg_id"]}},

    {"name": "create_s3_bucket", "description": "Create an S3 bucket.",
     "input_schema": {"type": "object", "properties": {"bucket_name": {"type": "string"}},
         "required": ["bucket_name"]}},

    {"name": "create_rds_subnet_group", "description": "Create a DB subnet group (needs 2+ subnets in different AZs).",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "subnet_ids": {"type": "array", "items": {"type": "string"}},
         "description": {"type": "string"}},
         "required": ["name", "subnet_ids"]}},

    {"name": "create_rds_instance", "description": "Create an RDS database instance.",
     "input_schema": {"type": "object", "properties": {
         "db_identifier": {"type": "string"}, "instance_class": {"type": "string"},
         "engine": {"type": "string"},
         "master_username": {"type": "string"}, "master_password": {"type": "string"},
         "subnet_group_name": {"type": "string"},
         "vpc_security_group_ids": {"type": "array", "items": {"type": "string"}},
         "allocated_storage": {"type": "integer"}, "engine_version": {"type": "string"},
         "multi_az": {"type": "boolean"}},
         "required": ["db_identifier", "instance_class", "engine", "master_username",
                      "master_password", "subnet_group_name", "vpc_security_group_ids"]}},
]


def to_openai_tools(tools):
    openai_tools = []
    for t in tools:
        params = dict(t["input_schema"])
        params.setdefault("properties", {})
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": params,
            },
        })
    return openai_tools


OPENAI_TOOLS = to_openai_tools(TOOLS)

SYSTEM_PROMPT = """You are an AWS infrastructure provisioning agent. Ask clarifying
questions one at a time for missing details, then execute tools in the correct order.
Always confirm the full plan with the user before creating billable resources.

Typical dependency chains:

NETWORKING (do this first, always needed):
  create_vpc -> create_subnet (x2, different AZs if RDS needed) ->
  create_internet_gateway -> create_route_table -> create_security_group

EC2:
  ...networking... -> create_key_pair -> run_ec2_instance

RDS:
  ...networking (2+ subnets in different AZs)... -> create_security_group (DB port e.g. 3306/5432) ->
  create_rds_subnet_group -> create_rds_instance

S3: standalone, no dependencies.

Use ids/ARNs returned from earlier tool calls as inputs to later ones. Never fabricate
resource ids. If a user only wants one type of resource, skip unrelated steps. For RDS,
confirm the user has or wants 2 AZs before proceeding."""


def run_agent():
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("AWS AI Agent (minimal, OpenAI) ready. Type your requirement (or 'quit' to exit).\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            break

        messages.append({"role": "user", "content": user_input})

        while True:
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=2000,
                tools=OPENAI_TOOLS,
                tool_choice="auto",
                messages=messages,
            )

            choice = response.choices[0]
            msg = choice.message

            if msg.content:
                print(f"\nAgent: {msg.content}\n")

            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls] if msg.tool_calls else None,
            })

            if choice.finish_reason != "tool_calls" or not msg.tool_calls:
                break

            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments or "{}")
                fn = TOOL_FUNCTIONS[fn_name]
                print(f"[Executing] {fn_name}({fn_args})")
                try:
                    result = fn(**fn_args)
                except Exception as e:
                    result = {"error": str(e)}
                print(f"[Result] {result}\n")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                })


if __name__ == "__main__":
    run_agent()
