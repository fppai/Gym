"""Wrapper for transforming observations."""
from typing import Any, Callable

import gym
from gym import ObservationWrapper


class TransformObservation(ObservationWrapper):
    """Transform the observation via an arbitrary function :attr:`f`.

    Example:
        >>> import gym
        >>> import numpy as np
        >>> env = gym.make('CartPole-v1')
        >>> env = TransformObservation(env, lambda obs: obs + 0.1*np.random.randn(*obs.shape))
        >>> env.reset()
        array([-0.08319338,  0.04635121, -0.07394746,  0.20877492])
    """

    def __init__(self, env: gym.Env, f: Callable[[Any], Any]):
        """Initialise the TransformObservation with an environment and a transform function :param:`f`.

        Args:
            env: The environment to apply the wrapper
            f: A function that transforms the observation
        """
        super().__init__(env)
        assert callable(f)
        self.f = f

    def observation(self, observation):
        """Transforms the observations with callable :attr:`f`.

        Args:
            observation: The observation to transform

        Returns:
            The transform observation
        """
        return self.f(observation)
