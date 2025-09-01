import os
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents import AgentsClient
from azure.ai.agents.models import DeepResearchTool
from dotenv import load_dotenv

load_dotenv()

project_client = AIProjectClient(
    endpoint=os.environ["PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

conn_id = project_client.connections.get(name=os.environ["BING_RESOURCE_NAME"]).id

deep_research_tool = DeepResearchTool(
    bing_grounding_connection_id=conn_id,
    deep_research_model=os.environ["DEEP_RESEARCH_MODEL_DEPLOYMENT_NAME"],
)


agent = project_client.agents.create_agent(
    model=os.environ["MODEL_DEPLOYMENT_NAME"],
    name="my-deep-research-agent",
    instructions="You are a helpful Agent that assists in researching scientific topics.",
    tools=deep_research_tool.definitions,
)
print(f"Agent created with ID: {agent.id}")

#        with open("agent_id.txt", "w") as f:
#            f.write(agent.id)
#        print("Agent ID saved to 'agent_id.txt'")
