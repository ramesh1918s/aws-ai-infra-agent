# AWS AI Infra Agent

A chat-driven AWS infrastructure provisioning agent. You describe what you
want in plain English (e.g. *"create a VPC with 2 subnets and launch a
t2.micro EC2 instance"*), an LLM decides which AWS API calls to make and in
what order, and the script executes real `boto3` calls against your AWS
account.

Three LLM backends are included — pick whichever one you have API access
to. They all drive the **same set of AWS tools** (VPC, EC2, S3, ECR, EKS,
Lambda, RDS, Load Balancer/DNS/CDN, Security & Secrets, DynamoDB/Aurora/
ElastiCache, SNS/SQS/EventBridge/API Gateway/Step Functions, CloudWatch/
CodeBuild/CodePipeline/CloudFormation, Glue/Athena/Kinesis, Bedrock/
SageMaker).

| Script | LLM Backend | Notes |
|---|---|---|
| `aws_ai_agent_gemini.py` | Google Gemini | **Recommended.** Free tier, no credit card required. |
| `aws_ai_agent_full.py` | OpenAI (GPT-4o) | Requires paid OpenAI billing/credits. |
| `aws_ai_agent_minimal.py` | OpenAI (GPT-4o) | Same as above but trimmed to core resources only (VPC/EC2/S3/RDS) — use this if you want a smaller, easier-to-read starting point. |

---

## 1. Prerequisites

- An AWS account with an IAM user/role that has permissions for the
  services you plan to provision
- AWS CLI v2 installed and configured (`aws configure` or an attached IAM
  role on your EC2 instance)
- Python 3.9+
- An API key for whichever LLM backend you choose (see below)

---

## 2. Setup (on a fresh EC2 instance or any Linux box)

```bash
# System update
sudo apt update && sudo apt upgrade -y

# Python + git
sudo apt install -y python3 python3-pip python3-venv git unzip

# AWS CLI v2
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version

# AWS credentials
aws configure
# Access Key ID, Secret Access Key, region (e.g. ap-south-1), output format (json)
# --- OR --- attach an IAM role to the EC2 instance and skip this step entirely

# Clone this repo
git clone <your-repo-url>
cd aws-ai-infra-agent

# Python virtual environment
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```
# this is added testing
---

## 3. Get an API key

### Gemini (recommended — free tier, no card required)
1. Go to https://aistudio.google.com/apikey
2. Sign in with a Google account → **Create API Key**

### OpenAI (paid — needs billing/credits)
1. Go to https://platform.openai.com/api-keys → **Create new secret key**
2. Add billing/credits at https://platform.openai.com/settings/organization/billing/overview

---

## 4. Set your API key

**Never paste your key directly into a terminal you'll screenshot/share.**
Edit `~/.bashrc` directly:

```bash
nano ~/.bashrc
```

Add one of these lines at the end (whichever backend you're using):

```bash
export GEMINI_API_KEY="your-gemini-key"
# or
export OPENAI_API_KEY="your-openai-key"
```

Save (`Ctrl+O`, `Enter`), exit (`Ctrl+X`), then reload:

```bash
source ~/.bashrc
```

---

## 5. Run

```bash
source venv/bin/activate

python3 aws_ai_agent_gemini.py     # Gemini backend
# or
python3 aws_ai_agent_full.py       # OpenAI backend, full tool set
# or
python3 aws_ai_agent_minimal.py    # OpenAI backend, core tools only
```

Example session:

```
You: Create a VPC with 2 subnets, an EC2 instance, and open port 22

Agent: To set this up I'll need a few details...
[...clarifying questions...]

You: yes, go ahead

[Executing] create_vpc({'cidr_block': '10.0.0.0/16', 'name': 'my-vpc'})
[Result] {'vpc_id': 'vpc-04d4b7744d0458bf5', 'cidr': '10.0.0.0/16'}
...
```

Type `quit` or `exit` to end the session.

---

## 6. Dependency chains the agent follows

```
NETWORKING (always first):
  create_vpc -> create_subnet (x2, different AZs if EKS/RDS/Aurora/ElastiCache/ALB needed)
             -> create_internet_gateway -> create_route_table -> create_security_group

EC2:
  ...networking... -> create_key_pair -> run_ec2_instance

EKS:
  ...networking (2+ subnets, different AZs)... -> create_eks_cluster_role
  -> create_eks_node_role -> create_eks_cluster -> check_eks_cluster_status (poll until ACTIVE)
  -> create_eks_nodegroup

Lambda:
  create_lambda_role -> create_lambda_function

RDS:
  ...networking (2+ subnets, different AZs)... -> create_security_group (DB port)
  -> create_rds_subnet_group -> create_rds_instance

Load Balancer + DNS + CDN:
  ...networking, EC2 instance(s) running... -> create_target_group -> create_load_balancer
  -> create_listener -> register_targets
  (optional) create_hosted_zone -> create_dns_record
  (optional) create_cloudfront_distribution
```

Full list of supported services and their dependency notes are in the
`SYSTEM_PROMPT` constant inside each script.

---

## 7. Known gotchas / troubleshooting log

These are real issues hit during development — kept here so you don't have
to rediscover them.

**"insufficient_quota" / 429 from OpenAI**
→ Your OpenAI account has no billing/credits attached. Add credits at
https://platform.openai.com/settings/organization/billing/overview.
This is a billing issue, not a code bug.

**"This model models/gemini-2.5-flash is no longer available to new users"**
→ Google periodically rotates model availability. Use the auto-updating
alias `gemini-flash-latest` instead of pinning a specific version. Check
https://ai.google.dev/gemini-api/docs/models for current model names if
this happens again.

**`ModuleNotFoundError: No module named 'google.generativeai'`**
→ You forgot to `source venv/bin/activate` before running, or forgot to
`pip install google-generativeai`. Confirm your prompt shows `(venv)`
before running the script.

**`Invalid type for parameter IpPermissions[0].FromPort, value: 22.0, type: <class 'float'>`**
→ Gemini's function-calling API returns *all* numeric arguments as floats
(e.g. port `22` arrives as `22.0`), but `boto3` strictly requires `int` for
fields like `FromPort`/`ToPort`/`AllocatedStorage`/etc. Fixed in
`aws_ai_agent_gemini.py` via a `_normalize_gemini_args()` helper that
recursively downcasts whole-number floats to `int` before any tool call
executes. If you fork this script and add new tools, this fix covers them
automatically — no per-function changes needed.

**Subnet AZ errors like `Value (us-east-1a) for parameter availabilityZone is invalid`**
→ Your AWS CLI/region is configured for a different region than the AZ
you asked for (e.g. region is `ap-south-1` but you asked for a `us-east-1`
AZ). Match AZs to your `REGION` constant at the top of the script, or let
the agent pick a default AZ in your configured region.

**AMI ID not found (`InvalidAMIID.NotFound`)**
→ AMI IDs are region-specific and change over time. Either look up the
current AMI ID for your region/OS in the AWS console/EC2 → AMI Catalog, or
ask the agent and let it retry with a corrected ID (Gemini/GPT will
generally self-correct on the next turn once it sees the error).

---

## 8. Security notes

- `.pem` key files generated by `create_key_pair()` are saved to the
  current directory — this repo's `.gitignore` excludes `*.pem` so you
  don't accidentally commit them. `chmod 400 your-key.pem` before using.
- Never commit API keys. Set them via `~/.bashrc` / environment variables,
  not in code.
- Many tools in this agent create **billable** AWS resources (EC2, RDS,
  EKS, NAT gateways, etc). The agent is instructed to confirm a full plan
  before executing, but always double-check what it's about to create.
- The Gemini free tier may use your prompts/responses to improve Google's
  models. Avoid putting sensitive data (secrets, real customer data) in
  your chat messages to the agent.

---

## 9. Repo structure

```
.
├── aws_ai_agent_gemini.py    # Gemini backend, full tool set (recommended)
├── aws_ai_agent_full.py      # OpenAI backend, full tool set
├── aws_ai_agent_minimal.py   # OpenAI backend, core tools only (VPC/EC2/S3/RDS)
├── requirements.txt
├── .gitignore
└── README.md
```
