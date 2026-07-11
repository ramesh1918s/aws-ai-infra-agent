"""
AWS AI Infra Agent -- GEMINI (Google AI) FULL VERSION
------------------------------------------------
Covers: Networking, EC2, S3, ECR, EKS, Lambda, RDS, Load Balancer/DNS/CDN,
Security & Secrets, Databases (DynamoDB/Aurora/ElastiCache), Serverless/
Messaging (SNS/SQS/EventBridge/API Gateway/Step Functions), Monitoring/
DevOps (CloudWatch/CodeBuild/CodePipeline/CloudFormation), Analytics
(Glue/Athena/Kinesis), AI/ML (Bedrock/SageMaker).

Uses Google's free-tier Gemini API instead of OpenAI/Anthropic.

Setup:
    pip install boto3 google-generativeai
    aws configure
    export GEMINI_API_KEY="your-key"

Run:
    python aws_ai_agent_gemini.py
"""

import json
import time
import zipfile
import io
import os
import boto3
import google.generativeai as genai

REGION = "ap-south-1"

# ---------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------
ec2 = boto3.client("ec2", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
ecr = boto3.client("ecr", region_name=REGION)
eks = boto3.client("eks", region_name=REGION)
iam = boto3.client("iam", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
rds = boto3.client("rds", region_name=REGION)
sts = boto3.client("sts", region_name=REGION)

elbv2 = boto3.client("elbv2", region_name=REGION)
route53 = boto3.client("route53")
cloudfront = boto3.client("cloudfront")

kms = boto3.client("kms", region_name=REGION)
secretsmanager = boto3.client("secretsmanager", region_name=REGION)
cognito = boto3.client("cognito-idp", region_name=REGION)
acm = boto3.client("acm", region_name=REGION)

dynamodb = boto3.client("dynamodb", region_name=REGION)
elasticache = boto3.client("elasticache", region_name=REGION)

sns = boto3.client("sns", region_name=REGION)
sqs = boto3.client("sqs", region_name=REGION)
events = boto3.client("events", region_name=REGION)
apigateway = boto3.client("apigateway", region_name=REGION)
sfn = boto3.client("stepfunctions", region_name=REGION)

cloudwatch = boto3.client("cloudwatch", region_name=REGION)
codebuild = boto3.client("codebuild", region_name=REGION)
codepipeline = boto3.client("codepipeline", region_name=REGION)
cloudformation = boto3.client("cloudformation", region_name=REGION)

glue = boto3.client("glue", region_name=REGION)
athena = boto3.client("athena", region_name=REGION)
kinesis = boto3.client("kinesis", region_name=REGION)

bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)
sagemaker = boto3.client("sagemaker", region_name=REGION)

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

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
# S3 / ECR
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


def create_ecr_repo(repo_name):
    resp = ecr.create_repository(repositoryName=repo_name)
    return {"repo_uri": resp["repository"]["repositoryUri"]}


# =====================================================================
# IAM HELPERS
# =====================================================================

def _create_role_if_needed(role_name, service_principal, managed_policy_arns):
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": service_principal},
            "Action": "sts:AssumeRole",
        }],
    }
    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        role_arn = resp["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

    for policy_arn in managed_policy_arns:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

    time.sleep(8)
    return role_arn


def create_eks_cluster_role(role_name="agent-eks-cluster-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "eks.amazonaws.com",
        ["arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"])}


def create_eks_node_role(role_name="agent-eks-node-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "ec2.amazonaws.com",
        ["arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
         "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
         "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"])}


def create_lambda_role(role_name="agent-lambda-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "lambda.amazonaws.com",
        ["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"])}


def create_codepipeline_role(role_name="agent-codepipeline-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "codepipeline.amazonaws.com",
        ["arn:aws:iam::aws:policy/AWSCodePipeline_FullAccess"])}


def create_codebuild_role(role_name="agent-codebuild-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "codebuild.amazonaws.com",
        ["arn:aws:iam::aws:policy/AWSCodeBuildAdminAccess",
         "arn:aws:iam::aws:policy/AmazonS3FullAccess"])}


def create_sagemaker_role(role_name="agent-sagemaker-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "sagemaker.amazonaws.com",
        ["arn:aws:iam::aws:policy/AmazonSageMakerFullAccess"])}


def create_stepfunctions_role(role_name="agent-stepfunctions-role"):
    return {"role_arn": _create_role_if_needed(
        role_name, "states.amazonaws.com",
        ["arn:aws:iam::aws:policy/AWSStepFunctionsFullAccess"])}


# =====================================================================
# EKS
# =====================================================================

def create_eks_cluster(cluster_name, subnet_ids, role_arn):
    if isinstance(subnet_ids, str):
        subnet_ids = [subnet_ids]
    eks.create_cluster(
        name=cluster_name,
        roleArn=role_arn,
        resourcesVpcConfig={"subnetIds": subnet_ids},
    )
    return {"cluster_name": cluster_name, "status": "CREATING (takes ~10-15 min, check with check_eks_cluster_status)"}


def create_eks_nodegroup(cluster_name, nodegroup_name, subnet_ids, node_role_arn,
                          instance_types=None, desired_size=2, min_size=1, max_size=3):
    if isinstance(subnet_ids, str):
        subnet_ids = [subnet_ids]
    if instance_types is None:
        instance_types = ["t3.medium"]
    eks.create_nodegroup(
        clusterName=cluster_name,
        nodegroupName=nodegroup_name,
        subnets=subnet_ids,
        nodeRole=node_role_arn,
        instanceTypes=instance_types,
        scalingConfig={"minSize": min_size, "maxSize": max_size, "desiredSize": desired_size},
    )
    return {"nodegroup_name": nodegroup_name, "status": "CREATING (cluster must be ACTIVE first)"}


def check_eks_cluster_status(cluster_name):
    resp = eks.describe_cluster(name=cluster_name)
    return {"status": resp["cluster"]["status"]}


# =====================================================================
# LAMBDA
# =====================================================================

def create_lambda_function(function_name, role_arn, handler="lambda_function.lambda_handler",
                            runtime="python3.12", code_source=None):
    if code_source is None:
        code_source = (
            "def lambda_handler(event, context):\n"
            "    return {'statusCode': 200, 'body': 'Hello from agent-created Lambda!'}\n"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lambda_function.py", code_source)
    buf.seek(0)
    resp = lambda_client.create_function(
        FunctionName=function_name,
        Runtime=runtime,
        Role=role_arn,
        Handler=handler,
        Code={"ZipFile": buf.read()},
        Timeout=15,
        MemorySize=128,
    )
    return {"function_name": function_name, "function_arn": resp["FunctionArn"]}


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
# LOAD BALANCING / DNS / CDN
# =====================================================================

def create_load_balancer(name, subnet_ids, sg_ids=None, scheme="internet-facing", lb_type="application"):
    if isinstance(subnet_ids, str):
        subnet_ids = [subnet_ids]
    kwargs = dict(Name=name, Subnets=subnet_ids, Scheme=scheme, Type=lb_type)
    if sg_ids and lb_type == "application":
        kwargs["SecurityGroups"] = sg_ids if isinstance(sg_ids, list) else [sg_ids]
    resp = elbv2.create_load_balancer(**kwargs)
    lb = resp["LoadBalancers"][0]
    return {"lb_arn": lb["LoadBalancerArn"], "dns_name": lb["DNSName"]}


def create_target_group(name, vpc_id, port, protocol="HTTP", target_type="instance"):
    resp = elbv2.create_target_group(
        Name=name, Protocol=protocol, Port=port, VpcId=vpc_id, TargetType=target_type
    )
    return {"tg_arn": resp["TargetGroups"][0]["TargetGroupArn"]}


def create_listener(lb_arn, tg_arn, port=80, protocol="HTTP"):
    resp = elbv2.create_listener(
        LoadBalancerArn=lb_arn, Protocol=protocol, Port=port,
        DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
    )
    return {"listener_arn": resp["Listeners"][0]["ListenerArn"]}


def register_targets(tg_arn, instance_ids):
    if isinstance(instance_ids, str):
        instance_ids = [instance_ids]
    elbv2.register_targets(TargetGroupArn=tg_arn, Targets=[{"Id": i} for i in instance_ids])
    return {"registered": instance_ids}


def create_hosted_zone(domain_name, caller_reference=None):
    caller_reference = caller_reference or str(time.time())
    resp = route53.create_hosted_zone(Name=domain_name, CallerReference=caller_reference)
    return {
        "hosted_zone_id": resp["HostedZone"]["Id"],
        "name_servers": resp["DelegationSet"]["NameServers"],
    }


def create_dns_record(hosted_zone_id, record_name, record_type, value, ttl=300):
    resp = route53.change_resource_record_sets(
        HostedZoneId=hosted_zone_id,
        ChangeBatch={"Changes": [{
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": record_name, "Type": record_type, "TTL": ttl,
                "ResourceRecords": [{"Value": value}],
            },
        }]},
    )
    return {"change_id": resp["ChangeInfo"]["Id"]}


def create_cloudfront_distribution(origin_domain, comment="agent-created distribution"):
    caller_reference = str(time.time())
    config = {
        "CallerReference": caller_reference,
        "Comment": comment,
        "Enabled": True,
        "Origins": {
            "Quantity": 1,
            "Items": [{
                "Id": "agent-origin",
                "DomainName": origin_domain,
                "CustomOriginConfig": {
                    "HTTPPort": 80, "HTTPSPort": 443,
                    "OriginProtocolPolicy": "http-only",
                },
            }],
        },
        "DefaultCacheBehavior": {
            "TargetOriginId": "agent-origin",
            "ViewerProtocolPolicy": "redirect-to-https",
            "ForwardedValues": {"QueryString": False, "Cookies": {"Forward": "none"}},
            "TrustedSigners": {"Enabled": False, "Quantity": 0},
            "MinTTL": 0,
        },
    }
    resp = cloudfront.create_distribution(DistributionConfig=config)
    return {
        "distribution_id": resp["Distribution"]["Id"],
        "domain_name": resp["Distribution"]["DomainName"],
    }


# =====================================================================
# SECURITY & SECRETS
# =====================================================================

def create_kms_key(description="agent-created KMS key", alias=None):
    resp = kms.create_key(Description=description)
    key_id = resp["KeyMetadata"]["KeyId"]
    if alias:
        kms.create_alias(AliasName=f"alias/{alias}", TargetKeyId=key_id)
    return {"key_id": key_id, "key_arn": resp["KeyMetadata"]["Arn"]}


def create_secret(name, secret_value, description="agent-created secret"):
    resp = secretsmanager.create_secret(Name=name, Description=description, SecretString=secret_value)
    return {"secret_arn": resp["ARN"]}


def create_cognito_user_pool(pool_name):
    resp = cognito.create_user_pool(PoolName=pool_name)
    return {"user_pool_id": resp["UserPool"]["Id"]}


def create_cognito_app_client(user_pool_id, client_name, generate_secret=False):
    resp = cognito.create_user_pool_client(
        UserPoolId=user_pool_id, ClientName=client_name, GenerateSecret=generate_secret
    )
    return {"client_id": resp["UserPoolClient"]["ClientId"]}


def request_acm_certificate(domain_name, validation_method="DNS"):
    resp = acm.request_certificate(DomainName=domain_name, ValidationMethod=validation_method)
    return {"certificate_arn": resp["CertificateArn"]}


# =====================================================================
# DATABASES
# =====================================================================

def create_dynamodb_table(table_name, partition_key, partition_key_type="S",
                           sort_key=None, sort_key_type="S", billing_mode="PAY_PER_REQUEST"):
    attrs = [{"AttributeName": partition_key, "AttributeType": partition_key_type}]
    schema = [{"AttributeName": partition_key, "KeyType": "HASH"}]
    if sort_key:
        attrs.append({"AttributeName": sort_key, "AttributeType": sort_key_type})
        schema.append({"AttributeName": sort_key, "KeyType": "RANGE"})
    resp = dynamodb.create_table(
        TableName=table_name, AttributeDefinitions=attrs, KeySchema=schema, BillingMode=billing_mode
    )
    return {"table_name": table_name, "status": resp["TableDescription"]["TableStatus"]}


def create_aurora_cluster(cluster_id, engine, master_username, master_password,
                           subnet_group_name, vpc_security_group_ids, engine_version=None,
                           instance_class="db.r6g.large", instance_count=1):
    kwargs = dict(
        DBClusterIdentifier=cluster_id, Engine=engine,
        MasterUsername=master_username, MasterUserPassword=master_password,
        DBSubnetGroupName=subnet_group_name,
        VpcSecurityGroupIds=(
            [vpc_security_group_ids] if isinstance(vpc_security_group_ids, str) else vpc_security_group_ids
        ),
    )
    if engine_version:
        kwargs["EngineVersion"] = engine_version
    rds.create_db_cluster(**kwargs)
    for i in range(instance_count):
        rds.create_db_instance(
            DBInstanceIdentifier=f"{cluster_id}-instance-{i + 1}",
            DBInstanceClass=instance_class, Engine=engine, DBClusterIdentifier=cluster_id,
        )
    return {"cluster_id": cluster_id, "status": "CREATING (cluster + instances take several minutes)"}


def create_elasticache_subnet_group(name, subnet_ids, description="agent cache subnet group"):
    if isinstance(subnet_ids, str):
        subnet_ids = [subnet_ids]
    elasticache.create_cache_subnet_group(
        CacheSubnetGroupName=name, CacheSubnetGroupDescription=description, SubnetIds=subnet_ids
    )
    return {"subnet_group_name": name}


def create_elasticache_cluster(cluster_id, engine, node_type, num_nodes=1,
                                subnet_group_name=None, security_group_ids=None):
    kwargs = dict(CacheClusterId=cluster_id, Engine=engine, CacheNodeType=node_type, NumCacheNodes=num_nodes)
    if subnet_group_name:
        kwargs["CacheSubnetGroupName"] = subnet_group_name
    if security_group_ids:
        kwargs["SecurityGroupIds"] = (
            security_group_ids if isinstance(security_group_ids, list) else [security_group_ids]
        )
    elasticache.create_cache_cluster(**kwargs)
    return {"cluster_id": cluster_id, "status": "CREATING"}


# =====================================================================
# SERVERLESS / MESSAGING
# =====================================================================

def create_sns_topic(topic_name):
    resp = sns.create_topic(Name=topic_name)
    return {"topic_arn": resp["TopicArn"]}


def subscribe_sns_topic(topic_arn, protocol, endpoint):
    resp = sns.subscribe(TopicArn=topic_arn, Protocol=protocol, Endpoint=endpoint)
    return {"subscription_arn": resp.get("SubscriptionArn", "pending confirmation")}


def create_sqs_queue(queue_name, fifo=False, visibility_timeout=30):
    attrs = {"VisibilityTimeout": str(visibility_timeout)}
    name = queue_name
    if fifo:
        name = queue_name if queue_name.endswith(".fifo") else f"{queue_name}.fifo"
        attrs["FifoQueue"] = "true"
    resp = sqs.create_queue(QueueName=name, Attributes=attrs)
    return {"queue_url": resp["QueueUrl"]}


def create_eventbridge_rule(rule_name, schedule_expression=None, event_pattern=None):
    kwargs = {"Name": rule_name}
    if schedule_expression:
        kwargs["ScheduleExpression"] = schedule_expression
    if event_pattern:
        kwargs["EventPattern"] = json.dumps(event_pattern)
    resp = events.put_rule(**kwargs)
    return {"rule_arn": resp["RuleArn"]}


def add_eventbridge_target(rule_name, target_arn, target_id="agent-target"):
    events.put_targets(Rule=rule_name, Targets=[{"Id": target_id, "Arn": target_arn}])
    return {"rule_name": rule_name, "target_arn": target_arn}


def create_rest_api(api_name, description="agent-created API"):
    resp = apigateway.create_rest_api(name=api_name, description=description)
    return {"api_id": resp["id"]}


def get_api_root_resource_id(api_id):
    resp = apigateway.get_resources(restApiId=api_id)
    root = next(r for r in resp["items"] if r["path"] == "/")
    return {"root_resource_id": root["id"]}


def create_api_resource_and_method(api_id, parent_resource_id, path_part, http_method="GET", lambda_arn=None):
    resp = apigateway.create_resource(restApiId=api_id, parentId=parent_resource_id, pathPart=path_part)
    resource_id = resp["id"]
    apigateway.put_method(restApiId=api_id, resourceId=resource_id, httpMethod=http_method, authorizationType="NONE")
    if lambda_arn:
        uri = f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{lambda_arn}/invocations"
        apigateway.put_integration(
            restApiId=api_id, resourceId=resource_id, httpMethod=http_method,
            type="AWS_PROXY", integrationHttpMethod="POST", uri=uri,
        )
    return {"resource_id": resource_id}


def deploy_api(api_id, stage_name="prod"):
    apigateway.create_deployment(restApiId=api_id, stageName=stage_name)
    return {"invoke_url": f"https://{api_id}.execute-api.{REGION}.amazonaws.com/{stage_name}"}


def create_step_function(name, definition, role_arn):
    resp = sfn.create_state_machine(name=name, definition=json.dumps(definition), roleArn=role_arn)
    return {"state_machine_arn": resp["stateMachineArn"]}


# =====================================================================
# MONITORING / DEVOPS
# =====================================================================

def create_cloudwatch_alarm(alarm_name, metric_name, namespace, statistic, comparison_operator,
                             threshold, period=300, evaluation_periods=1, dimensions=None, alarm_actions=None):
    kwargs = dict(
        AlarmName=alarm_name, MetricName=metric_name, Namespace=namespace, Statistic=statistic,
        ComparisonOperator=comparison_operator, Threshold=threshold,
        Period=period, EvaluationPeriods=evaluation_periods,
    )
    if dimensions:
        kwargs["Dimensions"] = dimensions
    if alarm_actions:
        kwargs["AlarmActions"] = alarm_actions
    cloudwatch.put_metric_alarm(**kwargs)
    return {"alarm_name": alarm_name}


def create_codebuild_project(project_name, source_location, role_arn, buildspec=None,
                              compute_type="BUILD_GENERAL1_SMALL",
                              image="aws/codebuild/amazonlinux2-x86_64-standard:5.0"):
    source = {"type": "S3", "location": source_location}
    if buildspec:
        source["buildspec"] = buildspec
    resp = codebuild.create_project(
        name=project_name, source=source, artifacts={"type": "NO_ARTIFACTS"},
        environment={"type": "LINUX_CONTAINER", "image": image, "computeType": compute_type},
        serviceRole=role_arn,
    )
    return {"project_arn": resp["project"]["arn"]}


def create_codepipeline(pipeline_name, role_arn, artifact_bucket, source_bucket, source_key, build_project_name):
    pipeline = {
        "name": pipeline_name,
        "roleArn": role_arn,
        "artifactStore": {"type": "S3", "location": artifact_bucket},
        "stages": [
            {"name": "Source", "actions": [{
                "name": "Source",
                "actionTypeId": {"category": "Source", "owner": "AWS", "provider": "S3", "version": "1"},
                "outputArtifacts": [{"name": "SourceOutput"}],
                "configuration": {"S3Bucket": source_bucket, "S3ObjectKey": source_key},
            }]},
            {"name": "Build", "actions": [{
                "name": "Build",
                "actionTypeId": {"category": "Build", "owner": "AWS", "provider": "CodeBuild", "version": "1"},
                "inputArtifacts": [{"name": "SourceOutput"}],
                "outputArtifacts": [{"name": "BuildOutput"}],
                "configuration": {"ProjectName": build_project_name},
            }]},
        ],
    }
    resp = codepipeline.create_pipeline(pipeline=pipeline)
    return {"pipeline_name": resp["pipeline"]["name"]}


def create_cloudformation_stack(stack_name, template_body, parameters=None, capabilities=None):
    kwargs = dict(StackName=stack_name, TemplateBody=template_body)
    if parameters:
        kwargs["Parameters"] = parameters
    if capabilities:
        kwargs["Capabilities"] = capabilities
    resp = cloudformation.create_stack(**kwargs)
    return {"stack_id": resp["StackId"], "status": "CREATING"}


# =====================================================================
# ANALYTICS
# =====================================================================

def create_glue_database(database_name):
    glue.create_database(DatabaseInput={"Name": database_name})
    return {"database_name": database_name}


def create_glue_crawler(crawler_name, role_arn, database_name, s3_target_path):
    glue.create_crawler(
        Name=crawler_name, Role=role_arn, DatabaseName=database_name,
        Targets={"S3Targets": [{"Path": s3_target_path}]},
    )
    return {"crawler_name": crawler_name}


def create_athena_workgroup(name, output_location):
    athena.create_work_group(Name=name, Configuration={"ResultConfiguration": {"OutputLocation": output_location}})
    return {"workgroup_name": name}


def run_athena_query(query, database, output_location, workgroup="primary"):
    resp = athena.start_query_execution(
        QueryString=query, QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location}, WorkGroup=workgroup,
    )
    return {"query_execution_id": resp["QueryExecutionId"]}


def create_kinesis_stream(stream_name, shard_count=1):
    kinesis.create_stream(StreamName=stream_name, ShardCount=shard_count)
    return {"stream_name": stream_name, "status": "CREATING"}


# =====================================================================
# AI / ML
# =====================================================================

def invoke_bedrock_model(model_id, prompt, max_tokens=512):
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = bedrock_runtime.invoke_model(modelId=model_id, body=body)
    payload = json.loads(resp["body"].read())
    return {"response": payload}


def create_sagemaker_notebook_instance(name, instance_type, role_arn):
    resp = sagemaker.create_notebook_instance(
        NotebookInstanceName=name, InstanceType=instance_type, RoleArn=role_arn
    )
    return {"notebook_arn": resp["NotebookInstanceArn"], "status": "CREATING"}


# =====================================================================
# TOOL REGISTRY
# =====================================================================

TOOL_FUNCTIONS = {
    "create_vpc": create_vpc, "create_subnet": create_subnet,
    "create_internet_gateway": create_internet_gateway, "create_route_table": create_route_table,
    "create_security_group": create_security_group, "create_key_pair": create_key_pair,
    "run_ec2_instance": run_ec2_instance, "create_s3_bucket": create_s3_bucket,
    "create_ecr_repo": create_ecr_repo,
    "create_eks_cluster_role": create_eks_cluster_role, "create_eks_node_role": create_eks_node_role,
    "create_eks_cluster": create_eks_cluster, "create_eks_nodegroup": create_eks_nodegroup,
    "check_eks_cluster_status": check_eks_cluster_status,
    "create_lambda_role": create_lambda_role, "create_lambda_function": create_lambda_function,
    "create_rds_subnet_group": create_rds_subnet_group, "create_rds_instance": create_rds_instance,
    "create_load_balancer": create_load_balancer, "create_target_group": create_target_group,
    "create_listener": create_listener, "register_targets": register_targets,
    "create_hosted_zone": create_hosted_zone, "create_dns_record": create_dns_record,
    "create_cloudfront_distribution": create_cloudfront_distribution,
    "create_kms_key": create_kms_key, "create_secret": create_secret,
    "create_cognito_user_pool": create_cognito_user_pool, "create_cognito_app_client": create_cognito_app_client,
    "request_acm_certificate": request_acm_certificate,
    "create_dynamodb_table": create_dynamodb_table, "create_aurora_cluster": create_aurora_cluster,
    "create_elasticache_subnet_group": create_elasticache_subnet_group,
    "create_elasticache_cluster": create_elasticache_cluster,
    "create_sns_topic": create_sns_topic, "subscribe_sns_topic": subscribe_sns_topic,
    "create_sqs_queue": create_sqs_queue, "create_eventbridge_rule": create_eventbridge_rule,
    "add_eventbridge_target": add_eventbridge_target, "create_rest_api": create_rest_api,
    "get_api_root_resource_id": get_api_root_resource_id,
    "create_api_resource_and_method": create_api_resource_and_method, "deploy_api": deploy_api,
    "create_stepfunctions_role": create_stepfunctions_role, "create_step_function": create_step_function,
    "create_cloudwatch_alarm": create_cloudwatch_alarm, "create_codebuild_role": create_codebuild_role,
    "create_codebuild_project": create_codebuild_project, "create_codepipeline_role": create_codepipeline_role,
    "create_codepipeline": create_codepipeline, "create_cloudformation_stack": create_cloudformation_stack,
    "create_glue_database": create_glue_database, "create_glue_crawler": create_glue_crawler,
    "create_athena_workgroup": create_athena_workgroup, "run_athena_query": run_athena_query,
    "create_kinesis_stream": create_kinesis_stream,
    "invoke_bedrock_model": invoke_bedrock_model, "create_sagemaker_role": create_sagemaker_role,
    "create_sagemaker_notebook_instance": create_sagemaker_notebook_instance,
}

TOOLS = [
    {"name": "create_vpc", "description": "Create a new VPC.",
     "input_schema": {"type": "object", "properties": {"cidr_block": {"type": "string"}, "name": {"type": "string"}}, "required": ["cidr_block"]}},
    {"name": "create_subnet", "description": "Create a subnet inside a VPC.",
     "input_schema": {"type": "object", "properties": {"vpc_id": {"type": "string"}, "cidr_block": {"type": "string"}, "az": {"type": "string"}, "name": {"type": "string"}, "public": {"type": "boolean"}}, "required": ["vpc_id", "cidr_block"]}},
    {"name": "create_internet_gateway", "description": "Create and attach an IGW to a VPC.",
     "input_schema": {"type": "object", "properties": {"vpc_id": {"type": "string"}, "name": {"type": "string"}}, "required": ["vpc_id"]}},
    {"name": "create_route_table", "description": "Create a route table with 0.0.0.0/0 via IGW, associate subnets.",
     "input_schema": {"type": "object", "properties": {"vpc_id": {"type": "string"}, "igw_id": {"type": "string"}, "subnet_ids": {"type": "array", "items": {"type": "string"}}, "name": {"type": "string"}}, "required": ["vpc_id", "igw_id", "subnet_ids"]}},
    {"name": "create_security_group", "description": "Create a security group with inbound port rules.",
     "input_schema": {"type": "object", "properties": {"vpc_id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}, "ports": {"type": "array", "items": {"type": "object", "properties": {"port": {"type": "integer"}, "cidr": {"type": "string"}}}}}, "required": ["vpc_id"]}},
    {"name": "create_key_pair", "description": "Create an EC2 key pair, save .pem locally.",
     "input_schema": {"type": "object", "properties": {"key_name": {"type": "string"}}, "required": ["key_name"]}},
    {"name": "run_ec2_instance", "description": "Launch an EC2 instance.",
     "input_schema": {"type": "object", "properties": {"ami_id": {"type": "string"}, "instance_type": {"type": "string"}, "key_name": {"type": "string"}, "subnet_id": {"type": "string"}, "sg_id": {"type": "string"}, "name": {"type": "string"}, "storage_gb": {"type": "integer"}}, "required": ["ami_id", "instance_type", "key_name", "subnet_id", "sg_id"]}},
    {"name": "create_s3_bucket", "description": "Create an S3 bucket.",
     "input_schema": {"type": "object", "properties": {"bucket_name": {"type": "string"}}, "required": ["bucket_name"]}},
    {"name": "create_ecr_repo", "description": "Create an ECR repository.",
     "input_schema": {"type": "object", "properties": {"repo_name": {"type": "string"}}, "required": ["repo_name"]}},
    {"name": "create_eks_cluster_role", "description": "Create IAM role for EKS cluster control plane.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_eks_node_role", "description": "Create IAM role for EKS worker nodes.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_eks_cluster", "description": "Create an EKS cluster (control plane). Needs cluster role + 2+ subnets in different AZs.",
     "input_schema": {"type": "object", "properties": {"cluster_name": {"type": "string"}, "subnet_ids": {"type": "array", "items": {"type": "string"}}, "role_arn": {"type": "string"}}, "required": ["cluster_name", "subnet_ids", "role_arn"]}},
    {"name": "create_eks_nodegroup", "description": "Create a managed nodegroup. Cluster must be ACTIVE first.",
     "input_schema": {"type": "object", "properties": {"cluster_name": {"type": "string"}, "nodegroup_name": {"type": "string"}, "subnet_ids": {"type": "array", "items": {"type": "string"}}, "node_role_arn": {"type": "string"}, "instance_types": {"type": "array", "items": {"type": "string"}}, "desired_size": {"type": "integer"}, "min_size": {"type": "integer"}, "max_size": {"type": "integer"}}, "required": ["cluster_name", "nodegroup_name", "subnet_ids", "node_role_arn"]}},
    {"name": "check_eks_cluster_status", "description": "Check if an EKS cluster is ACTIVE yet.",
     "input_schema": {"type": "object", "properties": {"cluster_name": {"type": "string"}}, "required": ["cluster_name"]}},
    {"name": "create_lambda_role", "description": "Create IAM execution role for Lambda.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_lambda_function", "description": "Create and deploy a Lambda function.",
     "input_schema": {"type": "object", "properties": {"function_name": {"type": "string"}, "role_arn": {"type": "string"}, "handler": {"type": "string"}, "runtime": {"type": "string"}, "code_source": {"type": "string"}}, "required": ["function_name", "role_arn"]}},
    {"name": "create_rds_subnet_group", "description": "Create a DB subnet group (needs 2+ subnets in different AZs).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "subnet_ids": {"type": "array", "items": {"type": "string"}}, "description": {"type": "string"}}, "required": ["name", "subnet_ids"]}},
    {"name": "create_rds_instance", "description": "Create an RDS database instance.",
     "input_schema": {"type": "object", "properties": {"db_identifier": {"type": "string"}, "instance_class": {"type": "string"}, "engine": {"type": "string"}, "master_username": {"type": "string"}, "master_password": {"type": "string"}, "subnet_group_name": {"type": "string"}, "vpc_security_group_ids": {"type": "array", "items": {"type": "string"}}, "allocated_storage": {"type": "integer"}, "engine_version": {"type": "string"}, "multi_az": {"type": "boolean"}}, "required": ["db_identifier", "instance_class", "engine", "master_username", "master_password", "subnet_group_name", "vpc_security_group_ids"]}},
    {"name": "create_load_balancer", "description": "Create an ALB or NLB in given subnets.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "subnet_ids": {"type": "array", "items": {"type": "string"}}, "sg_ids": {"type": "array", "items": {"type": "string"}}, "scheme": {"type": "string", "enum": ["internet-facing", "internal"]}, "lb_type": {"type": "string", "enum": ["application", "network"]}}, "required": ["name", "subnet_ids"]}},
    {"name": "create_target_group", "description": "Create a target group for a load balancer.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "vpc_id": {"type": "string"}, "port": {"type": "integer"}, "protocol": {"type": "string"}, "target_type": {"type": "string", "enum": ["instance", "ip", "lambda"]}}, "required": ["name", "vpc_id", "port"]}},
    {"name": "create_listener", "description": "Create a listener forwarding traffic from LB to a target group.",
     "input_schema": {"type": "object", "properties": {"lb_arn": {"type": "string"}, "tg_arn": {"type": "string"}, "port": {"type": "integer"}, "protocol": {"type": "string"}}, "required": ["lb_arn", "tg_arn"]}},
    {"name": "register_targets", "description": "Register EC2 instance(s) with a target group.",
     "input_schema": {"type": "object", "properties": {"tg_arn": {"type": "string"}, "instance_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["tg_arn", "instance_ids"]}},
    {"name": "create_hosted_zone", "description": "Create a Route 53 hosted zone for a domain.",
     "input_schema": {"type": "object", "properties": {"domain_name": {"type": "string"}}, "required": ["domain_name"]}},
    {"name": "create_dns_record", "description": "Create/update a DNS record in a Route 53 hosted zone.",
     "input_schema": {"type": "object", "properties": {"hosted_zone_id": {"type": "string"}, "record_name": {"type": "string"}, "record_type": {"type": "string"}, "value": {"type": "string"}, "ttl": {"type": "integer"}}, "required": ["hosted_zone_id", "record_name", "record_type", "value"]}},
    {"name": "create_cloudfront_distribution", "description": "Create a CloudFront distribution in front of an origin.",
     "input_schema": {"type": "object", "properties": {"origin_domain": {"type": "string"}, "comment": {"type": "string"}}, "required": ["origin_domain"]}},
    {"name": "create_kms_key", "description": "Create a KMS encryption key, optionally with an alias.",
     "input_schema": {"type": "object", "properties": {"description": {"type": "string"}, "alias": {"type": "string"}}}},
    {"name": "create_secret", "description": "Store a secret value in Secrets Manager.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "secret_value": {"type": "string"}, "description": {"type": "string"}}, "required": ["name", "secret_value"]}},
    {"name": "create_cognito_user_pool", "description": "Create a Cognito user pool for app authentication.",
     "input_schema": {"type": "object", "properties": {"pool_name": {"type": "string"}}, "required": ["pool_name"]}},
    {"name": "create_cognito_app_client", "description": "Create an app client for a Cognito user pool.",
     "input_schema": {"type": "object", "properties": {"user_pool_id": {"type": "string"}, "client_name": {"type": "string"}, "generate_secret": {"type": "boolean"}}, "required": ["user_pool_id", "client_name"]}},
    {"name": "request_acm_certificate", "description": "Request an SSL/TLS certificate from ACM for a domain.",
     "input_schema": {"type": "object", "properties": {"domain_name": {"type": "string"}, "validation_method": {"type": "string", "enum": ["DNS", "EMAIL"]}}, "required": ["domain_name"]}},
    {"name": "create_dynamodb_table", "description": "Create a DynamoDB table with a partition key and optional sort key.",
     "input_schema": {"type": "object", "properties": {"table_name": {"type": "string"}, "partition_key": {"type": "string"}, "partition_key_type": {"type": "string", "enum": ["S", "N", "B"]}, "sort_key": {"type": "string"}, "sort_key_type": {"type": "string", "enum": ["S", "N", "B"]}, "billing_mode": {"type": "string", "enum": ["PAY_PER_REQUEST", "PROVISIONED"]}}, "required": ["table_name", "partition_key"]}},
    {"name": "create_aurora_cluster", "description": "Create an Aurora DB cluster + instance(s). Needs 2+ subnets in different AZs.",
     "input_schema": {"type": "object", "properties": {"cluster_id": {"type": "string"}, "engine": {"type": "string", "enum": ["aurora-mysql", "aurora-postgresql"]}, "master_username": {"type": "string"}, "master_password": {"type": "string"}, "subnet_group_name": {"type": "string"}, "vpc_security_group_ids": {"type": "array", "items": {"type": "string"}}, "engine_version": {"type": "string"}, "instance_class": {"type": "string"}, "instance_count": {"type": "integer"}}, "required": ["cluster_id", "engine", "master_username", "master_password", "subnet_group_name", "vpc_security_group_ids"]}},
    {"name": "create_elasticache_subnet_group", "description": "Create a subnet group for ElastiCache (needs 2+ subnets).",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "subnet_ids": {"type": "array", "items": {"type": "string"}}, "description": {"type": "string"}}, "required": ["name", "subnet_ids"]}},
    {"name": "create_elasticache_cluster", "description": "Create a Redis or Memcached ElastiCache cluster.",
     "input_schema": {"type": "object", "properties": {"cluster_id": {"type": "string"}, "engine": {"type": "string", "enum": ["redis", "memcached"]}, "node_type": {"type": "string"}, "num_nodes": {"type": "integer"}, "subnet_group_name": {"type": "string"}, "security_group_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["cluster_id", "engine", "node_type"]}},
    {"name": "create_sns_topic", "description": "Create an SNS topic.",
     "input_schema": {"type": "object", "properties": {"topic_name": {"type": "string"}}, "required": ["topic_name"]}},
    {"name": "subscribe_sns_topic", "description": "Subscribe an endpoint to an SNS topic.",
     "input_schema": {"type": "object", "properties": {"topic_arn": {"type": "string"}, "protocol": {"type": "string"}, "endpoint": {"type": "string"}}, "required": ["topic_arn", "protocol", "endpoint"]}},
    {"name": "create_sqs_queue", "description": "Create an SQS queue (standard or FIFO).",
     "input_schema": {"type": "object", "properties": {"queue_name": {"type": "string"}, "fifo": {"type": "boolean"}, "visibility_timeout": {"type": "integer"}}, "required": ["queue_name"]}},
    {"name": "create_eventbridge_rule", "description": "Create an EventBridge rule (schedule or event pattern based).",
     "input_schema": {"type": "object", "properties": {"rule_name": {"type": "string"}, "schedule_expression": {"type": "string"}, "event_pattern": {"type": "object"}}, "required": ["rule_name"]}},
    {"name": "add_eventbridge_target", "description": "Attach a target to an EventBridge rule.",
     "input_schema": {"type": "object", "properties": {"rule_name": {"type": "string"}, "target_arn": {"type": "string"}, "target_id": {"type": "string"}}, "required": ["rule_name", "target_arn"]}},
    {"name": "create_rest_api", "description": "Create a new API Gateway REST API.",
     "input_schema": {"type": "object", "properties": {"api_name": {"type": "string"}, "description": {"type": "string"}}, "required": ["api_name"]}},
    {"name": "get_api_root_resource_id", "description": "Get the root resource id of a REST API.",
     "input_schema": {"type": "object", "properties": {"api_id": {"type": "string"}}, "required": ["api_id"]}},
    {"name": "create_api_resource_and_method", "description": "Add a resource path + HTTP method to a REST API.",
     "input_schema": {"type": "object", "properties": {"api_id": {"type": "string"}, "parent_resource_id": {"type": "string"}, "path_part": {"type": "string"}, "http_method": {"type": "string"}, "lambda_arn": {"type": "string"}}, "required": ["api_id", "parent_resource_id", "path_part"]}},
    {"name": "deploy_api", "description": "Deploy a REST API to a stage.",
     "input_schema": {"type": "object", "properties": {"api_id": {"type": "string"}, "stage_name": {"type": "string"}}, "required": ["api_id"]}},
    {"name": "create_stepfunctions_role", "description": "Create IAM execution role for Step Functions.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_step_function", "description": "Create a Step Functions state machine.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "definition": {"type": "object"}, "role_arn": {"type": "string"}}, "required": ["name", "definition", "role_arn"]}},
    {"name": "create_cloudwatch_alarm", "description": "Create a CloudWatch metric alarm.",
     "input_schema": {"type": "object", "properties": {"alarm_name": {"type": "string"}, "metric_name": {"type": "string"}, "namespace": {"type": "string"}, "statistic": {"type": "string"}, "comparison_operator": {"type": "string"}, "threshold": {"type": "number"}, "period": {"type": "integer"}, "evaluation_periods": {"type": "integer"}, "dimensions": {"type": "array", "items": {"type": "object"}}, "alarm_actions": {"type": "array", "items": {"type": "string"}}}, "required": ["alarm_name", "metric_name", "namespace", "statistic", "comparison_operator", "threshold"]}},
    {"name": "create_codebuild_role", "description": "Create IAM role for CodeBuild.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_codebuild_project", "description": "Create a CodeBuild project with an S3 source.",
     "input_schema": {"type": "object", "properties": {"project_name": {"type": "string"}, "source_location": {"type": "string"}, "role_arn": {"type": "string"}, "buildspec": {"type": "string"}, "compute_type": {"type": "string"}, "image": {"type": "string"}}, "required": ["project_name", "source_location", "role_arn"]}},
    {"name": "create_codepipeline_role", "description": "Create IAM role for CodePipeline.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_codepipeline", "description": "Create a minimal S3-source -> CodeBuild CodePipeline.",
     "input_schema": {"type": "object", "properties": {"pipeline_name": {"type": "string"}, "role_arn": {"type": "string"}, "artifact_bucket": {"type": "string"}, "source_bucket": {"type": "string"}, "source_key": {"type": "string"}, "build_project_name": {"type": "string"}}, "required": ["pipeline_name", "role_arn", "artifact_bucket", "source_bucket", "source_key", "build_project_name"]}},
    {"name": "create_cloudformation_stack", "description": "Create a CloudFormation stack from a template body.",
     "input_schema": {"type": "object", "properties": {"stack_name": {"type": "string"}, "template_body": {"type": "string"}, "parameters": {"type": "array", "items": {"type": "object"}}, "capabilities": {"type": "array", "items": {"type": "string"}}}, "required": ["stack_name", "template_body"]}},
    {"name": "create_glue_database", "description": "Create a Glue Data Catalog database.",
     "input_schema": {"type": "object", "properties": {"database_name": {"type": "string"}}, "required": ["database_name"]}},
    {"name": "create_glue_crawler", "description": "Create a Glue crawler pointed at an S3 path.",
     "input_schema": {"type": "object", "properties": {"crawler_name": {"type": "string"}, "role_arn": {"type": "string"}, "database_name": {"type": "string"}, "s3_target_path": {"type": "string"}}, "required": ["crawler_name", "role_arn", "database_name", "s3_target_path"]}},
    {"name": "create_athena_workgroup", "description": "Create an Athena workgroup with a default query output location.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "output_location": {"type": "string"}}, "required": ["name", "output_location"]}},
    {"name": "run_athena_query", "description": "Run a SQL query in Athena.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}, "database": {"type": "string"}, "output_location": {"type": "string"}, "workgroup": {"type": "string"}}, "required": ["query", "database", "output_location"]}},
    {"name": "create_kinesis_stream", "description": "Create a Kinesis data stream.",
     "input_schema": {"type": "object", "properties": {"stream_name": {"type": "string"}, "shard_count": {"type": "integer"}}, "required": ["stream_name"]}},
    {"name": "invoke_bedrock_model", "description": "Invoke a Bedrock foundation model (no infra to provision).",
     "input_schema": {"type": "object", "properties": {"model_id": {"type": "string"}, "prompt": {"type": "string"}, "max_tokens": {"type": "integer"}}, "required": ["model_id", "prompt"]}},
    {"name": "create_sagemaker_role", "description": "Create IAM role for SageMaker.",
     "input_schema": {"type": "object", "properties": {"role_name": {"type": "string"}}}},
    {"name": "create_sagemaker_notebook_instance", "description": "Create a SageMaker notebook instance.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "instance_type": {"type": "string"}, "role_arn": {"type": "string"}}, "required": ["name", "instance_type", "role_arn"]}},
]


def _clean_schema_for_gemini(schema):
    """Gemini's OpenAPI-subset schema doesn't like empty enum/extra keys in
    every case, but plain type/properties/required/items/enum/description
    works fine. This mostly passes the schema through, dropping properties
    with no 'type' declared (Gemini requires a type on every property)."""
    if not isinstance(schema, dict):
        return schema
    cleaned = {}
    for k, v in schema.items():
        if k == "properties" and isinstance(v, dict):
            cleaned_props = {}
            for pname, pschema in v.items():
                if isinstance(pschema, dict) and "type" not in pschema:
                    pschema = dict(pschema)
                    pschema["type"] = "string"
                cleaned_props[pname] = _clean_schema_for_gemini(pschema)
            cleaned[k] = cleaned_props
        elif k == "items" and isinstance(v, dict):
            cleaned[k] = _clean_schema_for_gemini(v)
        else:
            cleaned[k] = v
    return cleaned


def to_gemini_tools(tools):
    """Convert Anthropic-style tool schemas (name/description/input_schema)
    into Gemini's function_declarations format."""
    declarations = []
    for t in tools:
        params = _clean_schema_for_gemini(dict(t["input_schema"]))
        params.setdefault("properties", {})
        declarations.append({
            "name": t["name"],
            "description": t["description"],
            "parameters": params,
        })
    return [{"function_declarations": declarations}]


GEMINI_TOOLS = to_gemini_tools(TOOLS)

SYSTEM_PROMPT = """You are an AWS infrastructure provisioning agent. Ask clarifying
questions one at a time for missing details, then execute tools in the correct order.
Always confirm the full plan with the user before creating billable resources.

NETWORKING (do this first, always needed):
  create_vpc -> create_subnet (x2, different AZs if EKS/RDS/Aurora/ElastiCache/ALB needed) ->
  create_internet_gateway -> create_route_table -> create_security_group

EC2: ...networking... -> create_key_pair -> run_ec2_instance

EKS: ...networking (2+ subnets, different AZs)... -> create_eks_cluster_role ->
  create_eks_node_role -> create_eks_cluster -> check_eks_cluster_status (poll until ACTIVE) ->
  create_eks_nodegroup

Lambda: create_lambda_role -> create_lambda_function

RDS: ...networking (2+ subnets, different AZs)... -> create_security_group (DB port) ->
  create_rds_subnet_group -> create_rds_instance

S3 / ECR: standalone.

Load Balancer + DNS + CDN: ...networking, EC2 instance(s) running... ->
  create_target_group -> create_load_balancer -> create_listener -> register_targets
  (optional) create_hosted_zone -> create_dns_record
  (optional) create_cloudfront_distribution
  (optional, HTTPS) request_acm_certificate before create_listener

Security & Secrets: mostly standalone.
Databases: DynamoDB standalone; Aurora/ElastiCache need networking + subnet group + SG.
Serverless/Messaging: SNS/SQS standalone; EventBridge/API Gateway/Step Functions as documented.
Monitoring/DevOps: CloudWatch standalone; CodePipeline needs CodeBuild role+project first.
Analytics: Glue/Athena/Kinesis mostly standalone.
AI/ML: Bedrock is on-demand, no infra. SageMaker needs role first.

Use ids/ARNs returned from earlier tool calls as inputs to later ones. Never fabricate
resource ids. If a user only wants one type of resource, skip unrelated steps."""


MODEL_NAME = "gemini-flash-latest"  # auto-updating alias -> currently Gemini 3.5 Flash, free-tier eligible
# Fallback options if this alias ever misbehaves: "gemini-3-flash" or "gemini-2.5-flash-lite"


def _normalize_gemini_args(value):
    """Gemini's function-calling proto returns all numbers as floats (e.g.
    port 22 arrives as 22.0), but boto3 strictly rejects float where it
    expects int (FromPort, ToPort, storage sizes, counts, etc). This
    recursively walks dicts/lists/MapComposite/RepeatedComposite objects
    and downcasts whole-number floats to int."""
    # proto-plus MapComposite / RepeatedComposite behave like dict / list
    if hasattr(value, "items"):
        return {k: _normalize_gemini_args(v) for k, v in value.items()}
    if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)):
        return [_normalize_gemini_args(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def run_agent():
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
        tools=GEMINI_TOOLS,
    )
    chat = model.start_chat(history=[])
    print(f"AWS AI Agent (Gemini/{MODEL_NAME}) ready. Type your requirement (or 'quit' to exit).\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            break

        response = chat.send_message(user_input)

        while True:
            function_calls = []
            text_parts = []

            for part in response.candidates[0].content.parts:
                if hasattr(part, "function_call") and part.function_call and part.function_call.name:
                    function_calls.append(part.function_call)
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

            if text_parts:
                print(f"\nAgent: {''.join(text_parts)}\n")

            if not function_calls:
                break

            function_response_parts = []
            for fc in function_calls:
                fn_name = fc.name
                fn_args = _normalize_gemini_args(dict(fc.args)) if fc.args else {}
                fn = TOOL_FUNCTIONS[fn_name]
                print(f"[Executing] {fn_name}({fn_args})")
                try:
                    result = fn(**fn_args)
                except Exception as e:
                    result = {"error": str(e)}
                print(f"[Result] {result}\n")

                function_response_parts.append(
                    genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=fn_name,
                            response={"result": json.dumps(result)},
                        )
                    )
                )

            response = chat.send_message(function_response_parts)


if __name__ == "__main__":
    run_agent()
