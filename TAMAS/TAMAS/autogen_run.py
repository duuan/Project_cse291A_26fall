import asyncio
import os
import json
import argparse
import importlib
from autogen_ext.models.openai import AzureOpenAIChatCompletionClient
from autogen_agentchat.teams import MagenticOneGroupChat, RoundRobinGroupChat, Swarm

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.ui import Console
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_agentchat.conditions import ExternalTermination, TextMentionTermination, MaxMessageTermination

# Set up model client here

model_client = AzureOpenAIChatCompletionClient(
    model='gpt-4o',
)

parser = argparse.ArgumentParser(description="Input user query")
parser.add_argument("--json_data", type=str, required=True, help="Path to JSON input file")
parser.add_argument("--model", type=str, required=True, help="Model name to be used")
parser.add_argument("--config", type=str, required=True, help="Configuration name")
args = parser.parse_args()

if 'gpt' in args.model.lower():
    model_client = OpenAIChatCompletionClient(
        model=args.model,
        api_key=os.getenv("OPENAI_API_KEY"),
    )



tools_writing = importlib.import_module("tools_article_writing_agent")
tool_name_writing = ['write_headline',
    'draft_article_body',
    'optimize_readability',
    'summarize_long_content',
    'insert_metadata_and_tags',
    'insert_propaganda_phrases',
    'rewrite_article_with_bias']
tools_writing_agent = [getattr(tools_writing, tool) for tool in tool_name_writing if hasattr(tools_writing, tool)]

tools_distribution = importlib.import_module("tools_distribution_agent")
tool_name_distribution = ['post_on_x',
    'post_on_insta',
    'post_on_linkedin',
    'send_push_notifications',
    'send_newsletter',
    'publish_to_unverified_article']
tools_distribution_agent = [getattr(tools_distribution, tool) for tool in tool_name_distribution if hasattr(tools_distribution, tool)]

tools_fact_check = importlib.import_module("tools_fact_checking_agent")
tool_name_fact_check = ['cross_reference_with_reuters',
    'compare_with_official_sources',
    'detect_misinformation_patterns',
    'fetch_related_articles',
    'summarize_conflicting_claims',
    'leak_fact_check_logs',
    'tamper_fact_verdicts',
    'overwrite_evidence_summary']
tools_fact_checking_agent = [getattr(tools_fact_check, tool) for tool in tool_name_fact_check if hasattr(tools_fact_check, tool)]

tools_news_gathering = importlib.import_module("tools_news_gathering_agent")
tool_name_gathering = ['fetch_latest_rss_feeds',
    'scrape_news_websites',
    'monitor_twitter_trends',
    'track_subreddit_posts',
    'get_alerts_from_news_api',
    'exfiltrate_all_rss_sources',
    'inject_fake_trends',
    'exfiltrate_trending_subreddits',
    'poison_gathered_data']
tools_news_gathering_agent = [getattr(tools_news_gathering, tool) for tool in tool_name_gathering if hasattr(tools_news_gathering, tool)]

tool_mapping = {
    "Article Writing Agent": tools_writing_agent,
    "Article Writing Agent A": tools_writing_agent,
    "Article Writing Agent B": tools_writing_agent,
    "Distribution Agent": tools_distribution_agent,
    "Distribution Agent A": tools_distribution_agent,
    "Distribution Agent B": tools_distribution_agent,
    "News Gathering Agent": tools_news_gathering_agent,
    "News Gathering Agent A": tools_news_gathering_agent,
    "News Gathering Agent B": tools_news_gathering_agent,
    "Fact Checking Agent": tools_fact_checking_agent,
    "Fact Checking Agent A": tools_fact_checking_agent,
    "Fact Checking Agent B": tools_fact_checking_agent,
}

initialized_agents = {}
agent_list = []


async def main() -> None:
    try:
        with open(args.json_data,  "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON: {e}")
        return 


    for agent in data["agents"]:
    
        var_name = agent['agent_name'].lower().replace(" ", "_")  
        agent_name = var_name

        system_message = agent["agent_description"]

        tools = tool_mapping.get(agent['agent_name'], [])

        initialized_agents[var_name] = AssistantAgent(
            name=agent_name.replace(" ", "_"),
            model_client=model_client,
            tools=tools,
            system_message=system_message,
        )

        agent_list.append(initialized_agents[var_name])


    termination = MaxMessageTermination(50) | TextMentionTermination("TERMINATE")

    if args.config == "RoundRobin":
        team = RoundRobinGroupChat(agent_list, termination_condition=termination)
    elif args.config == "Swarm":
        team = Swarm(agent_list, termination_condition=termination)
    elif args.config == "Magentic_one":
        team = MagenticOneGroupChat(agent_list, model_client=model_client)
    else:
        print(f"Unknown config: {args.config}")
        return
    
    await Console(team.run_stream(task="Task: "+data['user query']))

asyncio.run(main())