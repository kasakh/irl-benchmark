import gym
import numpy as np
from typing import Callable, Dict, List

from irl_benchmark.config import IRL_CONFIG_DOMAINS
from irl_benchmark.irl.algorithms.base_algorithm import BaseIRLAlgorithm
from irl_benchmark.irl.reward.reward_function import FeatureBasedRewardFunction
from irl_benchmark.rl.algorithms.base_algorithm import BaseRLAlgorithm
from irl_benchmark.utils.wrapper_utils import get_transition_matrix


class MaxCausalEntIRL(BaseIRLAlgorithm):

    def __init__(self, env: gym.Env, expert_trajs: List[Dict[str, list]],
                 rl_alg_factory: Callable[[gym.Env], BaseRLAlgorithm],
                 config: dict):

        super(MaxCausalEntIRL, self).__init__(env, expert_trajs, rl_alg_factory,
                                        config)
        self.transition_matrix = get_transition_matrix(self.env)
        self.n_states, self.n_actions, _ = self.transition_matrix.shape
        self.feat_map = np.eye(self.n_states)

    def sa_visitations(self):
        """
        Given a list of trajectories in an mdp, computes the state-action
        visitation counts and the probability of a trajectory starting in state s.

            Arrays of shape (n_states, n_actions) and (n_states).
        """

        s0_count = np.zeros(self.n_states)
        sa_visit_count = np.zeros((self.n_states, self.n_actions))

        for traj in self.expert_trajs:
            # traj['states'][0] is the state of the first timestep of the trajectory.
            s0_count[traj['states'][0]] += 1

            for timestep in range(len(traj['actions'])):
                state = traj['states'][timestep]
                action = traj['actions'][timestep]

                sa_visit_count[state, action] += 1

        # Count into probability
        P0 = s0_count / len(self.expert_trajs)

        return sa_visit_count, P0

    def occupancy_measure(self, policy, P0, t_max=None, threshold=1e-6):

        """
        Computes occupancy measure of a MDP under a given time-constrained policy
        -- the expected discounted number of times that policy π visits state s in
        a given number of timesteps, as in Algorithm 9.3 of Ziebart's thesis:
        http://www.cs.cmu.edu/~bziebart/publications/thesis-bziebart.pdf.
        """

        if P0 is None:
            P0 = np.ones(self.n_states) / self.n_states
        d_prev = np.zeros_like(P0)

        t = 0

        diff = float("inf")

        while diff > threshold:

            d = np.copy(P0)

            for state in range(self.n_states):
                for action in range(self.n_actions):
                    # for all next_state reachable:

                    for next_state in range(self.n_states):
                        # probabilty of reaching next_state by taking action
                        prob = self.transition_matrix[state, action, next_state]
                        d[next_state] += self.config['gamma'] * d_prev[state] * policy[state, action] * prob

            diff = np.amax(abs(d_prev - d))  # maxima of the flattened array
            d_prev = np.copy(d)

            if t_max is not None:
                t += 1
                if t == t_max:
                    break
        return d_prev

    def train(self, no_irl_iterations: int,
              no_rl_episodes_per_irl_iteration: int,
              no_irl_episodes_per_irl_iteration: int
              ):

        """
        Finds theta, a reward parametrization vector (r[s] = features[s]'.*theta)
        that maximizes the log likelihood of the given expert trajectories,
        modelling the expert as a Boltzmann rational agent with given temperature.

        """

        sa_visit_count, P0 = self.sa_visitations()

        mean_s_visit_count = np.sum(sa_visit_count, 1) / len(self.expert_trajs)

        mean_feature_count = np.dot(self.feat_map.T, mean_s_visit_count)

        # initialize the parameters
        # theta = np.random.rand(self.feat_map.shape[1])

        reward_function = FeatureBasedRewardFunction(self.env, 'random')
        theta = reward_function.parameters

        agent = self.rl_alg_factory(self.env)

        irl_iteration_counter = 0

        while irl_iteration_counter < no_irl_iterations:
            irl_iteration_counter += 1

            if self.config['verbose']:
                print('IRL ITERATION ' + str(irl_iteration_counter))

            reward_function_estimate = FeatureBasedRewardFunction(self.env, theta)
            self.env.update_reward_function(reward_function_estimate)

            # compute policy
            agent.train(no_rl_episodes_per_irl_iteration)

            policy = agent.policy_array()
            state_values = agent.state_values
            q_values = agent.q_values

            # Log-Likelihood
            # l = np.sum(sa_visit_count * (q_values - state_values.T))  # check: broadcasting works as intended or not

            # occupancy measure
            d = self.occupancy_measure(policy=policy, P0=P0)

            # log-likeilihood gradient
            dl_dtheta = -(mean_feature_count - np.dot(self.feat_map.T, d))

            # graduate descent
            theta -= self.config['lr'] * dl_dtheta[:-1]

            print(theta)
        return theta


IRL_CONFIG_DOMAINS[MaxCausalEntIRL] = {
    'gamma': {
        'type': float,
        'min': 0.0,
        'max': 1.0,
        'default': 0.9,
    },

    'epsilon': {
        'type': float,
        'min': 0.0,
        'max': float('inf'),
        'default': 1e-6,
    },
    'verbose': {
        'type': bool,
        'default': True
    },
    'lr': {
        'type': float,
        'default': 0.02,
        'min': 0.000001,
        'max': 50
    }
}
