import shutil
import time, gym, copy, seaborn
import numpy as np
import tensorflow as tf
import matplotlib.pyplot as plt
from collections import defaultdict

import tqdm
from overcooked_ai_py.utils import save_pickle, load_pickle, load_dict_from_txt,save_dict_to_file
from overcooked_ai_py.agents.agent import AgentPair, RandomAgent, StayAgent#, RandomMoveAgent
from overcooked_ai_py.agents.benchmarking import AgentEvaluator
from human_aware_rl.utils import set_global_seed, prepare_nested_default_dict_for_pickle, common_keys_equal
from human_aware_rl.baselines_utils import get_agent_from_saved_model
from human_aware_rl.imitation.behavioural_cloning import get_bc_agent_from_saved#,get_part_bc_agent_from_saved,get_none_bc_agent_from_saved
from human_aware_rl.utils import get_max_iter
from human_aware_rl.ppo.ppo import get_ppo_agent
import os
import ast
import random
import argparse
PPO_DATA_DIR = "data/ppo_runs/"
# Visualization

def plot_pbt_runs(pbt_model_paths, seeds, single=False, save=False, show=False):
    """Plots sparse rewards"""
    for layout in pbt_model_paths.keys():
        logs_and_cfgs = get_logs(pbt_model_paths[layout], seeds=seeds)
        log, cfg = logs_and_cfgs[0]

        ep_rew_means = []
        for l, cfg in logs_and_cfgs:
            rews = np.array(l['ep_sparse_rew_mean'])
            ep_rew_means.append(rews)
        ep_rew_means = np.array(ep_rew_means)

        ppo_updates_per_pairing = int(cfg['PPO_RUN_TOT_TIMESTEPS'] / cfg['TOTAL_BATCH_SIZE'])
        x_axis = list(range(0, log['num_ppo_runs'] * cfg['PPO_RUN_TOT_TIMESTEPS'], cfg['PPO_RUN_TOT_TIMESTEPS'] // ppo_updates_per_pairing))
        plt.figure(figsize=(7,4.5))
        if single:
            for i in range(len(logs_and_cfgs)):
                plt.plot(x_axis, ep_rew_means[i], label=str(i))
            plt.legend()
        else:
            seaborn.tsplot(time=x_axis, data=ep_rew_means)
        plt.xlabel("Environment timesteps")
        plt.ylabel("Mean episode reward")
        plt.ticklabel_format(style='sci', axis='x', scilimits=(0,0))
        plt.tight_layout()
        if save: plt.savefig("rew_pbt_" + layout, bbox_inches='tight')
        if show: plt.show()

def get_logs(save_dir, seeds, agent_array=None):
    """
    Get training logs across seeds for all PBT runs. By default take the logs for
    agent0, but can specify custom agents through the `agent_array` parameter.
    """
    save_dir = PPO_DATA_DIR + save_dir + "/"
    logs_across_seeds = []
    if agent_array is None:
        agent_array = [0] * len(seeds)
    for seed_idx, seed in enumerate(seeds):
        seed_log = load_dict_from_txt(save_dir + "seed_{}/agent{}/logs".format(seed, agent_array[seed_idx]))
        seed_cfg = load_dict_from_txt(save_dir + "config")
        logs_across_seeds.append((seed_log, seed_cfg))
    return logs_across_seeds

def get_pbt_agent_from_config(save_dir, name, seed, agent_idx=0, best=False):
    if best:
        agent_folder = save_dir + '/seed_{}/{}/'.format(seed,name)
        pos_names = [int(i.replace("pbt_iter", "")) for i in os.listdir(agent_folder) if "pbt_iter" in i]
        pos_names = sorted(pos_names)
        agent_to_load_path = agent_folder + "best"
    else:
        if os.path.isfile(os.path.join(PPO_DATA_DIR, "best_info.txt")):
            with open(os.path.join(PPO_DATA_DIR, "best_info.txt"), 'r') as f:
                data = f.readlines()[0]
                data = ast.literal_eval(data)
            env = save_dir.split('/')[-1]
            index = data[env]["seed_{}".format(seed)][0] -1
            print("read from best_info.txt, the index is {}".format(index))
        else:
            print("read the last one")
            index = -1
        agent_folder = save_dir + '/seed_{}/main_pop/'.format(seed)
        pos_names = [int(i) for i in os.listdir(agent_folder)]
        pos_names = sorted(pos_names)
        pos_name = str(pos_names[index])
        agent_to_load_path = agent_folder + pos_name
    print("agent_to_load_path : {}".format(agent_to_load_path))
    return agent_to_load_path

# Evaluation

def evaluate_sp_ppo_and_bc(layout, ppo_sp_path, bc_test_path, num_rounds, seeds, best=False, display=False):
    sp_ppo_performance = defaultdict(lambda: defaultdict(list))

    agent_bc_test, bc_params = get_bc_agent_from_saved(bc_test_path)
    del bc_params["data_params"]
    del bc_params["mdp_fn_params"]
    # bc_params["selfish"] = False
    evaluator = AgentEvaluator(**bc_params)

    for seed in seeds:
        print(ppo_sp_path, seed, best)
        agent_ppo, _ = get_ppo_agent(ppo_sp_path, seed, best=best)

        ppo_and_ppo = evaluator.evaluate_agent_pair(AgentPair(agent_ppo, agent_ppo, allow_duplicate_agents=True), num_games=num_rounds, display=display)
        avg_ppo_and_ppo = np.mean(ppo_and_ppo['ep_returns'])
        sp_ppo_performance[layout]["PPO_SP+PPO_SP"].append(avg_ppo_and_ppo)

        # Evaluate with BC test
        ppo_and_bc = evaluator.evaluate_agent_pair(AgentPair(agent_ppo, agent_bc_test), num_games=num_rounds, display=display)
        avg_ppo_and_bc = np.mean(ppo_and_bc['ep_returns'])
        sp_ppo_performance[layout]["PPO_SP+BC_test_0"].append(avg_ppo_and_bc)

        bc_and_ppo = evaluator.evaluate_agent_pair(AgentPair(agent_bc_test, agent_ppo), num_games=num_rounds, display=display)
        avg_bc_and_ppo = np.mean(bc_and_ppo['ep_returns'])
        sp_ppo_performance[layout]["PPO_SP+BC_test_1"].append(avg_bc_and_ppo)

    return sp_ppo_performance

def evaluate_all_sp_ppo_models(ppo_sp_model_paths, bc_test_model_paths, num_rounds, seeds, best):
    ppo_sp_performance = {}
    print(ppo_sp_model_paths, bc_test_model_paths)
    for layout in ppo_sp_model_paths.keys():
        print(layout)
        layout_eval = evaluate_sp_ppo_and_bc(layout, ppo_sp_model_paths[layout], bc_test_model_paths[layout], num_rounds, seeds, best)
        ppo_sp_performance.update(dict(layout_eval))
    return prepare_nested_default_dict_for_pickle(ppo_sp_performance)

def evaluate_all_pbt_models(pbt_model_paths, best_bc_model_paths, num_rounds, best=False, name="pos_agent0"):
    pbt_performance = defaultdict(lambda: defaultdict(list))
    for layout in pbt_model_paths.keys():
        print(layout)
        pbt_performance = evaluate_pbt_for_layout(layout, num_rounds, pbt_performance, pbt_model_paths, best_bc_model_paths['test'], best=best,name=name)
    return prepare_nested_default_dict_for_pickle(pbt_performance)

def evaluate_pbt_for_layout(layout_name, num_rounds, pbt_performance, pbt_model_paths, best_test_bc_models, best=False,name="pos_agent0"):
    bc_agent, bc_params = get_bc_agent_from_saved(model_name=best_test_bc_models[layout_name])
    #bc_agent_part, _ = get_part_bc_agent_from_saved(model_name=best_test_bc_models[layout_name])
    ae = AgentEvaluator(mdp_params=bc_params["mdp_params"], env_params=bc_params["env_params"])
    pbt_save_dir = os.path.join(PPO_DATA_DIR, pbt_model_paths[layout_name][0])
    print(pbt_save_dir)
    pbt_config = load_dict_from_txt(os.path.join(pbt_save_dir, "config"))
    # bc_agent_random = RandomAgent(pbt_config["sim_threads"])
    # bc_agent_move = RandomMoveAgent(pbt_config["sim_threads"])
    # bc_agent_stay = StayAgent(pbt_config["sim_threads"])

    bc_agents = {"self": "self", 'bc_best': bc_agent}
    # bc_agents = {'bc_best': bc_agent}

    assert common_keys_equal(bc_params["mdp_params"], pbt_config["mdp_params"]), "Mdp params differed between PBT and BC models training"
    assert common_keys_equal(bc_params["env_params"], pbt_config["env_params"]), "Env params differed between PBT and BC models training"

    pbt_agents = [get_pbt_agent_from_config(pbt_save_dir, name, seed=seed, agent_idx=0, best=best) for seed in pbt_model_paths[layout_name][1]]
    pbt_performance = eval_pbt_over_seeds(pbt_agents, bc_agents, layout_name, num_rounds, pbt_performance, ae)
    return pbt_performance

def eval_pbt_over_seeds(pbt_agents, bc_agents, layout_name, num_rounds, pbt_performance, agent_evaluator):
    ae = agent_evaluator
    for i in range(len(pbt_agents)):
        agent_to_load_path = pbt_agents[i]
        agent = get_agent_from_saved_model(agent_to_load_path, 30)

        for name_bc, bc_agent in bc_agents.items():
            if name_bc == "self":
                print('Now we are evaluating the agent with {}....'.format(name_bc))
                pbt_and_bc = ae.evaluate_agent_pair(AgentPair(agent, agent, allow_duplicate_agents=True), num_games=num_rounds)
                avg_pbt_and_bc = np.mean(pbt_and_bc['ep_returns'])
                pbt_performance[layout_name]["PBT+{}_0".format(name_bc)].append(avg_pbt_and_bc)
            else:
                print('Now we are evaluating the agent with {}....'.format(name_bc))
                pbt_and_bc = ae.evaluate_agent_pair(AgentPair(agent, bc_agent), num_games=num_rounds)
                avg_pbt_and_bc = np.mean(pbt_and_bc['ep_returns'])
                pbt_performance[layout_name]["PBT+{}_0".format(name_bc)].append(avg_pbt_and_bc)

                bc_and_pbt = ae.evaluate_agent_pair(AgentPair(bc_agent, agent), num_games=num_rounds)
                avg_bc_and_pbt = np.mean(bc_and_pbt['ep_returns'])
                pbt_performance[layout_name]["PBT+{}_1".format(name_bc)].append(avg_bc_and_pbt)

    print(pbt_performance)
    return pbt_performance

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Process some integers.')
    parser.add_argument('--num', type=int, default=5)
    # parser.add_argument('--env', type=str, default=None)
    parser.add_argument('--name', type=str, default="agent0")
    parser.add_argument('--best', action='store_false')
    args = parser.parse_args()
    print(args)
    num_rounds = args.num
    print(num_rounds)
    # pbt_model_paths = {
    #     # "simple": "pbt_simple",
    #     "unident_s": "ppo_sp_unident_s",
    #     "random1": "ppo_sp_random1",
    #     # "random0": "pbt_random0",
    #     "random3": "ppo_sp_random3",
    # }
    ppo_model_paths = {
        "simple": "ppo_sp_simple",
        "unident_s": "ppo_sp_unident_s",
        "random1": "ppo_sp_random1",
        "random0": "ppo_sp_random0",
        "random3": "ppo_sp_random3",
    }
    model_paths_seeds = {}
    for key, value in ppo_model_paths.items():
        path = os.path.join("{}{}".format(PPO_DATA_DIR, value))
        seeds = []
        if os.path.isdir(path):
            for seed in os.listdir(path):
                if os.path.isdir(os.path.join(path, seed)):
                    seeds += [int(seed.split('d')[1])]
        if len(seeds) != 0:
            model_paths_seeds[key] = ["sp_{}".format(key), seeds]
    print(model_paths_seeds)
    best_bc_model_paths = load_pickle("./data/bc_runs/best_bc_model_paths")

    # Evaluating
    set_global_seed(512)
    pbt_performance = evaluate_all_sp_ppo_models(ppo_model_paths, best_bc_model_paths['test'], num_rounds, best=args.best, seeds=seeds)
    save_dict_to_file(pbt_performance, PPO_DATA_DIR + "our_isbest(" + "{})".format(args.best))

