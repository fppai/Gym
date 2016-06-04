import logging
import os

import numpy as np

from doom_py import DoomGame, Mode, Button, GameVariable, ScreenFormat, ScreenResolution, Loader
from gym import spaces
from gym.envs.doom import doom_env

logger = logging.getLogger(__name__)

class DoomDefendCenterEnv(doom_env.DoomEnv):
    """
    ------------ Training Mission 3 - Defend the Center ------------
    This map is designed to teach you how to kill and how to stay alive.
    You will also need to keep an eye on your ammunition level. You are only
    rewarded for kills, so figure out how to stay alive.

    The map is a circle with monsters in the middle. Monsters will
    respawn with additional health when killed. Kill as many as you can
    before you run out of ammo.

    Allowed actions:
        [0]  - ATTACK                           - Shoot weapon - Values 0 or 1
        [14] - TURN_RIGHT                       - Turn right - Values 0 or 1
        [15] - TURN_LEFT                        - Turn left - Values 0 or 1
    Note: see controls.md for details

    Rewards:
        +  1    - Killing the monster
        -  1    - Penalty for being killed

    Goal: 10 points
        Kill 10 monsters (you have 26 ammo)

    Ends when:
        - Player is dead
        - Timeout (60 seconds - 2100 frames)

    Actions:
    Either of
        1) action = [0, 1, 0]       # Recommended
            where parameter #1 is ATTACK (0 or 1)
            where parameter #2 is TURN_RIGHT (0 or 1)
            where parameter #3 is TURN_LEFT (0 or 1)
        or
        2) actions = [0] * 41       # To train for the Deathmatch level
           actions[0] = 0       # ATTACK
           actions[14] = 1      # TURN_RIGHT
           actions[15] = 0      # TURN_LEFT
    -----------------------------------------------------
    """
    def __init__(self):
        super(DoomDefendCenterEnv, self).__init__()
        package_directory = os.path.dirname(os.path.abspath(__file__))
        self.loader = Loader()
        self.game = DoomGame()
        self.game.load_config(os.path.join(package_directory, 'assets/defend_the_center.cfg'))
        self.game.set_vizdoom_path(self.loader.get_vizdoom_path())
        self.game.set_doom_game_path(self.loader.get_freedoom_path())
        self.game.set_doom_scenario_path(self.loader.get_scenario_path('defend_the_center.wad'))
        self.screen_height = 480                    # Must match .cfg file
        self.screen_width = 640                     # Must match .cfg file
        self.game.set_window_visible(False)
        self.viewer = None
        self.game.init()
        self.game.new_episode()

        # 3 allowed actions [0, 14, 15] (must match .cfg file)
        self.action_space = spaces.HighLow(np.matrix([[0, 1, 0]] * 36 + [[-10, 10, 0]] * 2 + [[0, 100, 0]] * 3))
        self.observation_space = spaces.Box(low=0, high=255, shape=(self.screen_height, self.screen_width, 3))
        self.action_space.allowed_actions = [0, 14, 15]

        self._seed()
