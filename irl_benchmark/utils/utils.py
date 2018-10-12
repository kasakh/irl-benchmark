import json
from collections import defaultdict
from copy import copy

import numpy as np
from gym.envs.toy_text.discrete import DiscreteEnv

from irl_benchmark.irl.reward.reward_function import FeatureBasedRewardFunction, TabularRewardFunction
import irl_benchmark.irl.reward.reward_wrapper as rew_wrapper
import irl_benchmark.irl.feature.feature_wrapper as feat_wrapper


def to_one_hot(hot_vals, max_val):
    '''Convert an int list of data into one-hot vectors.'''
    return np.eye(max_val)[np.array(hot_vals, dtype=np.uint32)]


def unwrap_env(env, until_class=None):
    '''Unwrap wrapped env until we get an instance that is a until_class.

    If cls is None we will unwrap all the way.
    '''
    if until_class is None:
        while hasattr(env, 'env'):
            env = env.env
        return env

    while hasattr(env, 'env') and not isinstance(env, until_class):
        env = env.env

    if not isinstance(env, until_class):
        raise ValueError(
            "Unwrapping env did not yield an instance of class {}".format(
                until_class))
    return env


def is_unwrappable_to(env, to_class):
    '''Check if env can be unwrapped to to_class.'''
    if isinstance(env, to_class):
        return True
    while hasattr(env, 'env'):
        env = env.env
        if isinstance(env, to_class):
            return True
    return False


def get_transition_matrix(env, with_absorbing_state=True):
    '''Gets transition matrix from discrete environment.'''

    env = unwrap_env(env, DiscreteEnv)

    n_states = env.observation_space.n
    if with_absorbing_state:
        # adding one state in the end of the table as absorbing state:
        n_states += 1
    n_actions = env.action_space.n

    table = np.zeros([n_states, n_actions, n_states])

    # Iterate over "from" states:
    for state, P_given_state in env.P.items():

        # Iterate over actions:
        for action, transitions in P_given_state.items():

            # Iterate over "to" states:
            for probability, next_state, _, done in transitions:
                table[state, action, next_state] += probability
                if done:
                    # map next_state to absorbing state:
                    assert with_absorbing_state is True
                    table[next_state, :, :] = 0.0
                    table[next_state, :, -1] = 1.0

    if with_absorbing_state:
        # absorbing state that is reached whenever done == True
        # only reaches itself for each action
        table[-1, :, -1] = 1.0

    return table


def get_reward_matrix(env, with_absorbing_state=True):
    '''Gets reward array from discrete environment.'''

    discrete_env = unwrap_env(env, DiscreteEnv)

    if is_unwrappable_to(env, rew_wrapper.RewardWrapper):
        reward_wrapper = unwrap_env(env, rew_wrapper.RewardWrapper)
        reward_function = reward_wrapper.reward_function
        P_based_on_reward_function = {}
        for (state, P_for_state) in unwrap_env(env, DiscreteEnv).P.items():
            P_based_on_reward_function[state] = {}
            for (action, P_for_state_action) in P_for_state.items():
                outcomes = []
                for outcome in P_for_state_action:
                    P_entry = list(outcome)
                    next_state = outcome[1]
                    if is_unwrappable_to_subclass_of(env, feat_wrapper.FeatureWrapper):
                        reward_input = unwrap_to_subclass_of(env, feat_wrapper.FeatureWrapper).features(state, action, next_state)
                    else:
                        reward_input = reward_wrapper.get_reward_input_for(state, action, next_state)
                    P_entry[2] = reward_function.reward(reward_input).item()
                    outcomes.append(tuple(P_entry))
                P_based_on_reward_function[state][action] = outcomes
        discrete_env.P = P_based_on_reward_function
        print(P_based_on_reward_function)

    n_states = env.observation_space.n
    if with_absorbing_state:
        n_states += 1
    n_actions = env.action_space.n

    # unwrap the discrete env from which transitions can be extracted:
    discrete_env = unwrap_env(env, DiscreteEnv)
    # by default this discrete env's variable P will be used to extract rewards
    correct_P = copy(discrete_env.P)

    if with_absorbing_state:
        # change P in a way that terminal states map to the absorbing state
        for (state, P_for_state) in correct_P.items():
            for (action, P_for_state_action) in P_for_state.items():
                if len(P_for_state_action) == 1 and P_for_state_action[0][1] == state \
                        and P_for_state_action[0][3] is True:
                    # state maps to itself, but should map to absorbing state
                    rewired_outcome = (1.0, n_states - 1, 0, True)
                    correct_P[state][action] = [rewired_outcome]

    # however, if there is a reward wrapper, we need to use the wrapped reward function:
    if is_unwrappable_to(env, rew_wrapper.RewardWrapper):
        # get the reward function:
        reward_wrapper = unwrap_env(env, rew_wrapper.RewardWrapper)
        reward_function = reward_wrapper.reward_function
        # re-calculate P based on reward function:
        P_based_on_reward_function = {}
        for (state, P_for_state) in correct_P.items():
            P_based_on_reward_function[state] = {}
            for (action, P_for_state_action) in P_for_state.items():
                outcomes = []
                for old_outcome in P_for_state_action:
                    outcome = list(copy(old_outcome))
                    next_state = outcome[1]
                    if with_absorbing_state and next_state == n_states - 1:
                        # hard coded: 0 reward when going to absorbing state
                        # (since the absorbing state is added artificially and
                        # not part of the wrapper's reward function)
                        outcome[2] = 0.0
                    else:
                        if isinstance(reward_function,
                                      FeatureBasedRewardFunction):
                            # reward function needs features as input
                            reward_input = unwrap_env(
                                env, feat_wrapper.FeatureWrapper).features(
                                    None, None, next_state)
                        elif isinstance(reward_function,
                                        TabularRewardFunction):
                            # reward function needs domain batch as input
                            assert reward_function.action_in_domain is False
                            assert reward_function.next_state_in_domain is False
                            reward_input = reward_wrapper.get_reward_input_for(
                                None, None, next_state)
                        else:
                            raise ValueError(
                                'The RewardWrapper\'s reward_function is' +
                                'of not supported type ' +
                                str(type(reward_function)))
                        # update the reward part of the outcome
                        outcome[2] = reward_function.reward(
                            reward_input).item()
                    outcomes.append(tuple(outcome))
                P_based_on_reward_function[state][action] = outcomes
        correct_P = P_based_on_reward_function

    rewards = np.zeros([n_states, n_actions])

    # Iterate over "from" states:
    for s, P_given_state in correct_P.items():

        # Iterate over actions:
        for a, transitions in P_given_state.items():

            # Iterate over "to" states:
            for proba, sp, r, done in transitions:
                rewards[s, a] += r * proba
    if with_absorbing_state:
        rewards[-1, :] = 0.0
    return rewards


class MetricsLogger():
    '''Listens for metrics to be stored as json.

    Metrics can be stored once per run or once per training step. The
    simplest usage is to load the jsons of relevant runs, select
    metrics and convert to a pandas DataFrames.
    '''

    def __init__(self):
        self.metrics = defaultdict(lambda: [])

    def log_metric(self, name, value):
        self.metrics[name].append(value)

    def save(self, path):
        with open(path, 'wt') as f:
            json.dump(self.metrics, f)


def sigma(array):
    '''Replace negative entries of array w/ -1, others w/ 1.

    Returns modified array.
    '''
    array = array.copy()
    array[array >= 0] = 1
    array[array < 0] = -1
    return array


def avg_undiscounted_return(trajs):
    '''Return average undiscounted true return of trajs.

    Args:
    trajs -- `list` of dictionaries w/ keys 'true_rewards' and 'rewards'
    '''
    total_true_reward = 0
    if len(trajs[0]['true_rewards']) > 0:
        for traj in trajs:
            total_true_reward += np.sum(traj['true_rewards'])
    else:
        for traj in trajs:
            total_true_reward += np.sum(traj['rewards'])
    avg_undiscounted_return = total_true_reward / len(trajs)
    return avg_undiscounted_return
