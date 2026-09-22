sudo apt update

sudo apt upgrade -y

sudo apt install python3 python3-pip git -y

python3 --version

pip3 --version

mkdir ai-linux

cd ai-linux

pip3 install openai python-dotenv


nano .env

OPENAI_API_KEY=your_openai_api_key_here


import os
import subprocess

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SAFE_COMMANDS = {
    "df -h",
    "free -h",
    "uptime",

    "whoami",
    "pwd",
    "ls",
    "ls -la",
    "date",
    "hostname",
    "uname -a"
}

SYSTEM_PROMPT = """
You are an expert Linux administrator.

The user will ask Linux questions.

Reply with ONLY ONE Linux command.

Do not explain.

Do not use markdown.

Only use one of these commands:

df -h
free -h
uptime
whoami
pwd
ls
ls -la
date
hostname
uname -a
"""

while True:

    question = input("\nAsk Linux Question : ")

    if question.lower() == "exit":
        break

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
    )

    command = response.choices[0].message.content.strip()

    print("\nAI Generated Command")
    print(command)

    if command not in SAFE_COMMANDS:
        print("Blocked: command is not in the approved list.")
        continue

    output = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
    )

    print("\nOutput\n")
    print(output.stdout)




mkdir ai-linux-explainer
cd ai-linux-explainer

python3 -m venv venv
source venv/bin/activate

pip install google-genai python-dotenv

GEMINI_API_KEY=YOUR_GEMINI_API_KEY

GEMINI_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx



import os
import socket
import platform
import shutil
import psutil
from dotenv import load_dotenv
from google import genai

# ----------------------------
# Load API Key
# ----------------------------
load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

# ----------------------------
# Collect EC2 Information
# ----------------------------

hostname = socket.gethostname()

os_name = platform.platform()

cpu = psutil.cpu_percent(interval=1)

memory = psutil.virtual_memory()

disk = shutil.disk_usage("/")

boot = psutil.boot_time()

report = f"""

Hostname : {hostname}

Operating System : {os_name}

CPU Usage : {cpu} %

Memory Total : {round(memory.total/1024/1024/1024,2)} GB

Memory Used : {round(memory.used/1024/1024/1024,2)} GB

Memory Free : {round(memory.available/1024/1024/1024,2)} GB

Disk Total : {round(disk.total/1024/1024/1024,2)} GB

Disk Used : {round(disk.used/1024/1024/1024,2)} GB

Disk Free : {round(disk.free/1024/1024/1024,2)} GB

"""

print("\nCollected System Information...\n")

print(report)

# ----------------------------
# Ask Gemini
# ----------------------------

prompt = f"""
You are an AWS Cloud Engineer.

Analyze this EC2 server.

Provide

1. Overall Health

2. CPU Analysis

3. Memory Analysis

4. Disk Analysis

5. Recommendations

6. Any Risks

Server Report

{report}
"""

response = client.models.generate_content(

    model="gemini-2.5-flash",

    contents=prompt

)

print("="*70)

print("AI ANALYSIS")

print("="*70)

print(response.text)













import os

from dotenv import load_dotenv

from google import genai

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

topic = input("Interview Topic : ")

prompt = f"""

Ask AWS interview questions on

{topic}

One by one.

"""

response = client.models.generate_content(

model="gemini-2.5-flash",

contents=prompt

)

print(response.text)



import os

from dotenv import load_dotenv

from google import genai

load_dotenv()

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

logs = open("app.log").read()

prompt = f"""
Analyze these logs.

Find

Errors

Warnings

Root Cause

Recommendations

Logs

{logs}
"""

response = client.models.generate_content(

    model="gemini-2.5-flash",

    contents=prompt

)

print(response.text)


APP.LOG

2026-07-15 09:00:01 INFO  Application started
2026-07-15 09:00:02 INFO  Loading configuration file
2026-07-15 09:00:03 INFO  Connecting to database mysql-prod
2026-07-15 09:00:04 INFO  Database connection successful

2026-07-15 09:01:10 INFO  User login request received
2026-07-15 09:01:11 INFO  User authenticated successfully

2026-07-15 09:05:22 WARNING Disk usage on /var reached 82%

2026-07-15 09:10:10 INFO  Processing customer order #1001
2026-07-15 09:10:12 ERROR Payment gateway timeout after 30 seconds

2026-07-15 09:10:13 INFO  Retrying payment request
2026-07-15 09:10:18 INFO  Payment successful

2026-07-15 09:20:15 WARNING CPU utilization exceeded 85%

2026-07-15 09:30:10 INFO  Processing customer order #1002
2026-07-15 09:30:12 ERROR Unable to connect to Redis cache
2026-07-15 09:30:13 ERROR Redis connection refused
2026-07-15 09:30:14 INFO  Falling back to database

2026-07-15 09:40:05 WARNING Memory usage exceeded 75%

2026-07-15 09:45:22 INFO  Running scheduled backup

2026-07-15 09:45:30 ERROR Backup failed
2026-07-15 09:45:31 ERROR No space left on device

2026-07-15 09:50:11 INFO  User logout successful

2026-07-15 09:55:01 INFO  Application health check started
2026-07-15 09:55:02 WARNING Health check response time 4.2 seconds

2026-07-15 10:00:00 INFO  Application running

#this line  added testing process






Amazon EC2 stands for Elastic Compute Cloud.

An EC2 Instance is a virtual server in AWS.

Amazon Machine Image (AMI) is a template used to launch EC2 instances.

Security Groups act as virtual firewalls.

Key Pair is used to login into Linux EC2 instances.

Elastic IP provides a static public IP.

EBS Volume is persistent storage.

Instance Types:
t2.micro
t3.micro
m5.large

User Data is used to execute shell scripts during launch.

Auto Scaling automatically adds or removes EC2 instances.

Load Balancer distributes traffic among EC2 instances.




import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

model = genai.GenerativeModel("gemini-2.5-flash")

# Read knowledge base
with open("knowledge/ec2.txt", "r") as f:
    knowledge = f.read()


def retrieve(question):

    question = question.lower()

    lines = knowledge.split("\n")

    result = []

    for line in lines:
        if any(word in line.lower() for word in question.split()):
            result.append(line)

    return "\n".join(result)


while True:

    question = input("\nAsk EC2 Question : ")

    if question.lower() == "exit":
        break

    context = retrieve(question)

    prompt = f"""

You are an AWS Trainer.

Answer only using the below context.

Context:

{context}

Question:

{question}

"""

    response = model.generate_content(prompt)

    print("\nAnswer\n")

    print(response.text)











