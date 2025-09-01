# Azure AI Deep Research Sample

This sample serves as a reference for developers who want to leverage **Azure AI Foundry's Deep Research** capabilities. It provides:

- **Interactive UI integration** using Chainlit
- A **polling-based approach** for reliably handling long-running research tasks
- **Progress visibility** and **traceable references**, essential for enterprise-grade AI solutions

By using this sample, you can accelerate the development of AI research assistants and reduce integration complexity.

## Overview

This sample demonstrates how to integrate **Chainlit** with **Azure AI Foundry's Deep Research tool (`o3-deep-research`)** using a **non-streaming (polling) approach**.

The Deep Research tool in Azure AI Foundry combines web search with citation-based reasoning to generate comprehensive, evidence-backed answers. It uses **Grounding with Bing Search** to retrieve external information.

## Key Features

- Integration of Chainlit with Azure AI Agents service using the asynchronous Python client
- Adoption of **polling mode** instead of streaming for stable responses
- Clear progress display in the UI
- References are programmatically appended, avoiding model hallucinations

## Prerequisites

Follow the official prerequisites here:  
[Azure Deep Research Prerequisites](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/deep-research-samples?pivots=python#prerequisites)

## Tested Environment

This sample has been verified with:

- Python: 3.12.10
- chainlit: 2.6.8
- python-dotenv: 1.1.1
- azure-identity: 1.24.0
- azure-ai-projects: 1.1.0b2
- azure-ai-agents: 1.2.0b1
- azure-core: 1.35.0

### Upgrade Notes

- Check official documentation and release notes before upgrading any package
- Newer versions may introduce breaking changes
- If issues occur after upgrading, revert to the tested versions above

## Setup Instructions

### 1. Set Up Azure Deep Research Tool

Follow the official guide:  
[Azure AI Foundry Deep Research Setup](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/deep-research)

### 2. Configure Authentication

Set up Azure SDK for Python authentication:  
[Azure Python SDK Authentication](https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication/overview)

### 3. Install Python Packages

```bash
pip install python-dotenv azure-identity
pip install --pre azure-ai-projects
pip install chainlit
```

### 4. Configure Environment Variables

Create a `.env` file in your project directory and set the following variables:

```env
PROJECT_ENDPOINT=<Your Azure AI Project endpoint>
MODEL_DEPLOYMENT_NAME=<Arbitration model deployment name>
DEEP_RESEARCH_MODEL_DEPLOYMENT_NAME=<Deep Research model deployment name>
BING_RESOURCE_NAME=<Bing resource name>
AGENT_ID=<Agent ID from Step 5>
```

### 5. Create the Agent

Run the agent creation script to generate your AGENT_ID.
```bash
python create_agent.py
```

Copy the generated `AGENT_ID` from the output and add it to your `.env` file.

### 6. Launch the Chat UI

Start the Chainlit application to begin using the Deep Research capabilities.
```bash
chainlit run app.py
```

## Troubleshooting

If the sample does not work as expected:

1. **Recreate your virtual environment** as recommended in the official documentation
2. **Verify package versions** match the tested versions listed above
3. **Check authentication setup** and ensure all required permissions are granted
4. **Validate environment variables** are correctly set in your `.env` file

For additional support, refer to:
- [Official Documentation](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/deep-research-samples?pivots=python)
- [GitHub Issues](https://github.com/Azure/azure-sdk-for-python/issues/41935)

---

**Note**: This sample is designed for development and testing purposes. For production deployments, ensure proper security configurations and follow Azure best practices.