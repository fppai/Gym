"""Implementation of a space that represents the cartesian product of other spaces as a dictionary."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Dict as TypingDict
from typing import Optional

import numpy as np

from gym.spaces.space import Space
from gym.utils import seeding


class Dict(Space[TypingDict[str, Space]], Mapping):
    """A dictionary of simpler spaces.

    Elements of this space are (ordered) dictionaries of elements from the simpler spaces.

    Example usage::

        >>> observation_space = spaces.Dict({"position": spaces.Discrete(2), "velocity": spaces.Discrete(3)})
        >>> observation_space.sample()
        OrderedDict([('position', 1), ('velocity', 2)])

    Example usage [nested]::

        >>> spaces.Dict(
        ...     {
        ...         "ext_controller": spaces.MultiDiscrete((5, 2, 2)),
        ...         "inner_state": spaces.Dict(
        ...             {
        ...                 "charge": spaces.Discrete(100),
        ...                 "system_checks": spaces.MultiBinary(10),
        ...                 "job_status": spaces.Dict(
        ...                     {
        ...                         "task": spaces.Discrete(5),
        ...                         "progress": spaces.Box(low=0, high=100, shape=()),
        ...                     }
        ...                 ),
        ...             }
        ...         ),
        ...     }
        ... )

    It can be convenient to use ``Dict`` spaces if you want to make complex observations or actions more human-readable.
    Usually, it will be not be possible to use elements of this space directly in learning code. However, you can easily
    convert `Dict` observations to flat arrays by using a ``FlattenObservation`` wrapper. Similar wrappers can be
    implemented to deal with `Dict` actions.
    """

    def __init__(
        self,
        spaces: dict[str, Space] | None = None,
        seed: Optional[dict | int | seeding.RandomNumberGenerator] = None,
        **spaces_kwargs: Space,
    ):
        """Constructor of `Dict` space.

        This space can be instantiated in one of two ways: Either you pass a dictionary
        of spaces to `__init__` via the ``spaces`` argument, or you pass the spaces as separate
        keyword arguments (where you will need to avoid the keys ``spaces`` and ``seed``)

        Example::

            >>> spaces.Dict({"position": spaces.Box(-1, 1, shape=(2,)), "color": spaces.Discrete(3)})
            Dict(color:Discrete(3), position:Box(-1.0, 1.0, (2,), float32))
            >>> spaces.Dict(position=spaces.Box(-1, 1, shape=(2,)), color=spaces.Discrete(3))
            Dict(color:Discrete(3), position:Box(-1.0, 1.0, (2,), float32))

        Args:
            spaces: A dictionary of spaces. This specifies the structure of the `Dict` space
            seed: Optionally, you can use this argument to seed the RNG that is used to sample from the space
            **spaces_kwargs: If `spaces` is `None`, you need to pass the simpler spaces as keyword arguments, as described above.
        """
        assert (spaces is None) or (
            not spaces_kwargs
        ), "Use either Dict(spaces=dict(...)) or Dict(foo=x, bar=z)"

        if spaces is None:
            spaces = spaces_kwargs
        if isinstance(spaces, dict) and not isinstance(spaces, OrderedDict):
            try:
                spaces = OrderedDict(sorted(spaces.items()))
            except TypeError:  # raise when sort by different types of keys
                spaces = OrderedDict(spaces.items())
        if isinstance(spaces, Sequence):
            spaces = OrderedDict(spaces)

        assert isinstance(spaces, OrderedDict), "spaces must be a dictionary"

        self.spaces = spaces
        for space in spaces.values():
            assert isinstance(
                space, Space
            ), "Values of the dict should be instances of gym.Space"
        super().__init__(
            None, None, seed  # type: ignore
        )  # None for shape and dtype, since it'll require special handling

    def seed(self, seed: Optional[dict | int] = None) -> list:
        """Seed the PRNG of this space and all subspaces."""
        seeds = []
        if isinstance(seed, dict):
            for key, seed_key in zip(self.spaces, seed):
                assert key == seed_key, print(
                    "Key value",
                    seed_key,
                    "in passed seed dict did not match key value",
                    key,
                    "in spaces Dict.",
                )
                seeds += self.spaces[key].seed(seed[seed_key])
        elif isinstance(seed, int):
            seeds = super().seed(seed)
            try:
                subseeds = self.np_random.choice(
                    np.iinfo(int).max,
                    size=len(self.spaces),
                    replace=False,  # unique subseed for each subspace
                )
            except ValueError:
                subseeds = self.np_random.choice(
                    np.iinfo(int).max,
                    size=len(self.spaces),
                    replace=True,  # we get more than INT_MAX subspaces
                )

            for subspace, subseed in zip(self.spaces.values(), subseeds):
                seeds.append(subspace.seed(int(subseed))[0])
        elif seed is None:
            for space in self.spaces.values():
                seeds += space.seed(seed)
        else:
            raise TypeError("Passed seed not of an expected type: dict or int or None")

        return seeds

    def sample(self) -> dict:
        """Generates a single random sample from this space.

        The sample is an ordered dictionary of independent samples from the simpler spaces.
        """
        return OrderedDict([(k, space.sample()) for k, space in self.spaces.items()])

    def contains(self, x) -> bool:
        """Return boolean specifying if x is a valid member of this space."""
        if not isinstance(x, dict) or len(x) != len(self.spaces):
            return False
        for k, space in self.spaces.items():
            if k not in x:
                return False
            if not space.contains(x[k]):
                return False
        return True

    def __getitem__(self, key):
        """Get the space that is associated to `key`."""
        return self.spaces[key]

    def __setitem__(self, key, value):
        """Set the space that is associated to `key`."""
        self.spaces[key] = value

    def __iter__(self):
        """Iterator through the keys of the subspaces."""
        yield from self.spaces

    def __len__(self) -> int:
        """Gives the number of simpler spaces that make up the `Dict` space."""
        return len(self.spaces)

    def __repr__(self) -> str:
        """Gives a string representation of this space."""
        return (
            "Dict("
            + ", ".join([str(k) + ":" + str(s) for k, s in self.spaces.items()])
            + ")"
        )

    def to_jsonable(self, sample_n: list) -> dict:
        """Convert a batch of samples from this space to a JSONable data type."""
        # serialize as dict-repr of vectors
        return {
            key: space.to_jsonable([sample[key] for sample in sample_n])
            for key, space in self.spaces.items()
        }

    def from_jsonable(self, sample_n: dict[str, list]) -> list:
        """Convert a JSONable data type to a batch of samples from this space."""
        dict_of_list: dict[str, list] = {}
        for key, space in self.spaces.items():
            dict_of_list[key] = space.from_jsonable(sample_n[key])
        ret = []
        n_elements = len(next(iter(dict_of_list.values())))
        for i in range(n_elements):
            entry = {}
            for key, value in dict_of_list.items():
                entry[key] = value[i]
            ret.append(entry)
        return ret
