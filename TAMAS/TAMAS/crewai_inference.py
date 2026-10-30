from tqdm import tqdm
import subprocess  
import os 
import json
import tempfile
import time
import argparse
    
parser = argparse.ArgumentParser(description="")
parser.add_argument("--attack", type=str, required=True, help="Attack to be executed [byzantine, colluding, contradicting, DPI, IPI, impersonation]")
parser.add_argument("--scenario", type=str, required=True, help="Scenario to be used [education, finance, healthcare, legal, news]")
parser.add_argument("--config", type=str, required=True, help="Configuration name [Centralized, Decentralized]")
parser.add_argument("--model", type=str, required=True, help="Model name to be used")
args = parser.parse_args()

attack = args.attack
scenario = args.scenario
config = args.config
model = args.model
    
logs_dir = "./logs/"
os.makedirs(logs_dir, exist_ok=True)
    
    
with open(f"./data/{attack}/{scenario}_{attack}.json", 'r') as f:
    data = json.load(f)

for i, task in tqdm(enumerate(data), total=len(data), desc="Processing Data Points"):

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp_json:
        json.dump(task, temp_json)
        temp_json_path = temp_json.name  
    
    log_file = f"{logs_dir}/{attack}/{config}/{attack}_{scenario}_{i}.txt"
    with open(log_file, "w") as log:
        process = subprocess.Popen(
                ["python3", "crewai_run.py", "--json_data", temp_json_path, "--model", args.model, "--scenario", scenario, "--config", config],
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True
            )
    
        process.communicate()       
    os.remove(temp_json_path)