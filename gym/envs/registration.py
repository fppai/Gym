import re
import sys
import copy
import difflib
import importlib
import importlib.util
import contextlib
from typing import Callable, Type, Optional, Union, Tuple, Generator, Sequence, cast

if sys.version_info < (3, 10):
    import importlib_metadata as metadata  # type: ignore
else:
    import importlib.metadata as metadata

from dataclasses import dataclass, field, InitVar
from collections import defaultdict
from collections.abc import MutableMapping

from gym import error, logger, Env
from gym.envs.__relocated__ import internal_env_relocation_map


def load(name: str) -> Type:
    mod_name, attr_name = name.split(":")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, attr_name)
    return fn


def parse_env_id(id: str) -> Tuple[Optional[str], str, Optional[int]]:
    """Parse environment ID string format.

    This format is true today, but it's *not* an official spec.
    [username/](env-name)-v(version)    env-name is group 1, version is group 2

    2016-10-31: We're experimentally expanding the environment ID format
    to include an optional username.
    """
    pattern = re.compile(
        r"^(?:(?P<namespace>[\w:-]+)\/)?(?:(?P<name>[\w:.-]+?))(?:-v(?P<version>\d+))?$"
    )
    match = pattern.fullmatch(id)
    if not match:
        raise error.Error(
            f"Malformed environment ID: {id}."
            f"(Currently all IDs must be of the form {pattern}.)"
        )
    namespace, name, version = match.group("namespace", "name", "version")
    if version is not None:
        version = int(version)

    return namespace, name, version


@dataclass
class EnvSpec:
    """A specification for a particular instance of the environment. Used
    to register the parameters for official evaluations.

    Args:
        id: The official environment ID
        entry_point: The Python entrypoint of the environment class (e.g. module.name:Class)
        reward_threshold: The reward threshold before the task is considered solved
        nondeterministic: Whether this environment is non-deterministic even after seeding
        max_episode_steps: The maximum number of steps that an episode can consist of
        order_enforce: Whether to wrap the environment in an orderEnforcing wrapper
        kwargs: The kwargs to pass to the environment class

    """

    init_id: InitVar[str]
    entry_point: Optional[Union[Callable, str]] = None
    reward_threshold: Optional[int] = None
    nondeterministic: bool = False
    max_episode_steps: Optional[int] = None
    order_enforce: Optional[bool] = True
    kwargs: dict = field(default_factory=dict)

    namespace: Optional[str] = field(init=False)
    name: str = field(init=False)
    version: Optional[int] = field(init=False)

    def __post_init__(self, id):
        # Initialize namespace, name, version
        self.namespace, self.name, self.version = parse_env_id(id)

    @property
    def id(self) -> str:
        """
        `id` is an InitVar meaning it's only used at initialization to parse
        the namespace, name, and version. This means we can define the dynamic
        property `id` to construct the `id` from the parsed fields. This has the
        benefit that we update the fields and obtain a dynamic id.
        """
        namespace = "" if self.namespace is None else f"{self.namespace}/"
        name = self.name
        version = "" if self.version is None else f"-v{self.version}"
        return f"{namespace}{name}{version}"

    def make(self, **kwargs) -> Env:
        """Instantiates an instance of the environment with appropriate kwargs"""
        if self.entry_point is None:
            raise error.Error(
                f"Attempting to make deprecated env {self.id}. "
                "(HINT: is there a newer registered version of this env?)"
            )
        _kwargs = self.kwargs.copy()
        _kwargs.update(kwargs)

        if callable(self.entry_point):
            env = self.entry_point(**_kwargs)
        else:
            cls = load(self.entry_point)
            env = cls(**_kwargs)

        # Make the environment aware of which spec it came from.
        spec = copy.deepcopy(self)
        spec.kwargs = _kwargs
        env.unwrapped.spec = spec
        if env.spec.max_episode_steps is not None:
            from gym.wrappers.time_limit import TimeLimit

            env = TimeLimit(env, max_episode_steps=env.spec.max_episode_steps)
        else:
            if self.order_enforce:
                from gym.wrappers.order_enforcing import OrderEnforcing

                env = OrderEnforcing(env)
        return env


class EnvSpecTree(MutableMapping):
    """
    The EnvSpecTree provides a dict-like mapping object
    from environment IDs to specifications.

    The EnvSpecTree is backed by a tree-like structure.
    The environment ID format is [{namespace}/]{name}-v{version}.

    The tree has multiple root nodes corresponding to a namespace.
    The children of a namespace node corresponds to the environment name.
    Furthermore, each name has a mapping from versions to specifications.
    It looks like the following,

    {
        None: {
            MountainCar: {
                0: EnvSpec(...),
                1: EnvSpec(...)
            }
        },
        ALE: {
            Tetris: {
                5: EnvSpec(...)
            }
        }
    }

    The tree-structure isn't user-facing and the EnvSpecTree will act
    like a dictionary. For example, to lookup an environment ID:

        ```
        specs = EnvSpecTree()

        specs["My/Env-v0"] = EnvSpec(...)
        assert specs["My/Env-v0"] == EnvSpec(...)

        assert specs.tree["My"]["Env"]["0"] == specs["My/Env-v0"]
        ```
    """

    def __init__(self):
        # Initialize the tree as a nested sequence of defaultdicts
        self.tree = defaultdict(lambda: defaultdict(dict))
        self._length = 0

    def versions(self, namespace: Optional[str], name: str) -> Sequence[EnvSpec]:
        """
        Returns the versions associated with a namespace and name.

        Note: This function takes into account environment relocations.
        For example, `versions(None, "Breakout")` will return,
            ```
            [
                EnvSpec(namespace=None, name="Breakout", version=0),
                EnvSpec(namespace=None, name="Breakout", version=4),
                EnvSpec(namespace="ALE", name="Breakout", version=5)
            ]
            ```
        Notice the last environment which is outside of the requested namespace.
        This only applies to environments which are in the `internal_env_relocation_map`.
        See `gym/envs/__relocated__.py` for more info.
        """
        self._assert_name_exists(namespace, name)

        versions = list(self.tree[namespace][name].values())

        if namespace is None and name in internal_env_relocation_map:
            relocated_namespace, _ = internal_env_relocation_map[name]
            try:
                self._assert_name_exists(relocated_namespace, name)
                versions += list(self.tree[relocated_namespace][name].values())
            except error.UnregisteredEnv:
                pass

        return versions

    def names(self, namespace: Optional[str]) -> Sequence[str]:
        """
        Returns all the environment names associated with a namespace.
        """
        self._assert_namespace_exists(namespace)
        return list(self.tree[namespace].keys())

    def namespaces(self) -> Sequence[str]:
        """
        Returns all the namespaces contained in the tree.
        """
        return list(filter(None, self.tree.keys()))

    def __iter__(self) -> Generator[str, None, None]:
        # Iterate through the structure and generate the IDs contained in the tree.
        for namespace, names in self.tree.items():
            for name, versions in names.items():
                for version, spec in versions.items():
                    assert spec.namespace == namespace
                    assert spec.name == name
                    assert spec.version == version
                    yield spec.id

    def _assert_namespace_exists(self, namespace: Optional[str]) -> None:
        if namespace in self.tree:
            return

        message = f"Namespace `{namespace}` does not exist."
        if namespace:
            suggestions = difflib.get_close_matches(namespace, self.namespaces())
            if suggestions:
                message += f" Did you mean: `{suggestions[0]}`?"
            else:
                message += f" Have you installed the proper package for `{namespace}`?"
        raise error.NamespaceNotFound(message)

    def _assert_name_exists(self, namespace: Optional[str], name: str) -> None:
        self._assert_namespace_exists(namespace)
        if name in self.tree[namespace]:
            return

        if namespace is None and name in internal_env_relocation_map:
            relocated_namespace, relocated_package = internal_env_relocation_map[name]
            message = f"The environment `{name}` has been moved out of Gym to the package `{relocated_package}`."

            # Check if the package is installed
            # If not instruct the user to install the package and then how to instantiate the env
            if importlib.util.find_spec(relocated_package) is None:
                message += f" Please install the package via `pip install {relocated_package}`."

            # Otherwise the user should be able to instantiate the environment directly
            if namespace != relocated_namespace:
                message += f" You can instantiate the new namespaced environment as `{relocated_namespace}/{name}`."
        # If the environment hasn't been relocated we'll construct a generic error message
        else:
            message = f"Environment `{name}` doesn't exist"
            if namespace is not None:
                message += f" in namespace `{namespace}`"
            message += "."
            suggestions = difflib.get_close_matches(name, self.names(namespace), n=1)
            if suggestions:
                message += f" Did you mean: `{suggestions[0]}`?"
        # Throw the error
        raise error.NameNotFound(message)

    def _assert_version_exists(
        self, namespace: Optional[str], name: str, version: Optional[int]
    ):
        self._assert_name_exists(namespace, name)
        if version in self.tree[namespace][name]:
            return

        # Construct the appropriate exception.
        # If the version is less than the latest version
        # then we throw an error.DeprecatedEnv exception.
        # Otherwise we throw error.VersionNotFound.
        all_versions = self.tree[namespace][name].values()
        latest_spec = max(all_versions, key=lambda spec: spec.version, default=None)

        if version is not None:
            message = f"Environment version `v{version}` for `"
        else:
            message = "The default version for `"

        if namespace is not None:
            message += f"{namespace}/"
        message += f"{name}` "

        # If this version doesn't exist but there exists a later version
        # we should warn the user this version is deprecated.
        if (
            latest_spec
            and latest_spec.version is not None
            and version is not None
            and version < latest_spec.version
        ):
            message += "is deprecated. "
            message += f"Please use the latest version `v{latest_spec.version}`."
            raise error.DeprecatedEnv(message)
        # Otherwise we've asked for a version that doesn't exist.
        else:
            message += f"{name} could not be found. "
            if all_versions:
                all_versions_sorted = sorted(
                    all_versions, key=lambda spec: spec.version
                )
                message += "Valid versions are: [ "
                message += ", ".join(
                    map(lambda spec: f"`v{spec.version}`", all_versions_sorted)
                )
                message += " ]."
            else:
                message += "This environment appears to be unversioned."
            raise error.VersionNotFound(message)

    def __getitem__(self, key: str) -> EnvSpec:
        # Get an item from the tree.
        # We first parse the components so we can look up the
        # appropriate environment ID.
        namespace, name, version = parse_env_id(key)
        self._assert_version_exists(namespace, name, version)

        return self.tree[namespace][name][version]

    def __setitem__(self, key: str, value: EnvSpec) -> None:
        # Insert an item into the tree.
        # First we parse the components to get the path
        # for insertion.
        namespace, name, version = parse_env_id(key)
        self.tree[namespace][name][version] = value
        # Increase the size
        self._length += 1

    def __delitem__(self, key: str) -> None:
        # Delete an item from the tree.
        # First parse the components so we can follow the
        # path to delete.
        namespace, name, version = parse_env_id(key)
        self._assert_version_exists(namespace, name, version)

        # Remove the envspec with this version.
        self.tree[namespace][name].pop(version)
        # Remove the name if it's empty.
        if len(self.tree[namespace][name]) == 0:
            self.tree[namespace].pop(name)
        # Remove the namespace if it's empty.
        if len(self.tree[namespace]) == 0:
            self.tree.pop(namespace)
        # Decrease the size
        self._length -= 1

    def __contains__(self, key: str) -> bool:
        # Check if the tree contains a path for this key.
        namespace, name, version = parse_env_id(key)
        if (
            namespace in self.tree
            and name in self.tree[namespace]
            and version in self.tree[namespace][name]
        ):
            return True
        return False

    def __repr__(self) -> str:
        # Construct a tree-like representation structure
        # so we can easily look at the contents of the tree.
        tree_repr = ""
        for namespace, names in self.tree.items():
            # For each namespace we'll iterate over the names
            root = namespace is None
            # Insert a separator if we're between depths
            if len(tree_repr) > 0:
                tree_repr += "│\n"
            # if this isn't the root we'll display the namespace
            if not root:
                tree_repr += f"├──{str(namespace)}\n"

            # Construct the namespace string so we can print this for
            # our children.
            namespace = f"{namespace}/" if namespace is not None else ""
            for name_idx, (name, versions) in enumerate(names.items()):
                # If this isn't the root we'll have to increase our
                # depth, i.e., insert some space
                if not root:
                    tree_repr += "│   "
                # If this is the last item make sure we use the
                # termination character. Otherwise use the nested
                # character.
                if name_idx == len(names) - 1:
                    tree_repr += "└──"
                else:
                    tree_repr += "├──"
                # Print the namespace and the name
                # and get ready to print the versions.
                tree_repr += f"{namespace}{name}: [ "
                # Print each version comma separated
                for version_idx, version in enumerate(versions.keys()):
                    if version is not None:
                        tree_repr += f"v{version}"
                    else:
                        tree_repr += ""
                    if version_idx < len(versions) - 1:
                        tree_repr += ", "
                tree_repr += " ]\n"

        return tree_repr

    def __len__(self):
        # Return the length of the container
        return self._length


class EnvRegistry:
    """Register an env by ID. IDs remain stable over time and are
    guaranteed to resolve to the same environment dynamics (or be
    desupported). The goal is that results on a particular environment
    should always be comparable, and not depend on the version of the
    code that was running.
    """

    def __init__(self):
        self.env_specs = EnvSpecTree()
        self._ns: Optional[str] = None

    def make(self, path: str, **kwargs) -> Env:
        if len(kwargs) > 0:
            logger.info("Making new env: %s (%s)", path, kwargs)
        else:
            logger.info("Making new env: %s", path)
        spec = self.spec(path)

        # Get all versions of this spec.
        versions = self.env_specs.versions(spec.namespace, spec.name)

        # We check what the latest version of the environment is and display a warning
        # if the user is attempting to initialize an older version.
        latest_spec = max(
            filter(lambda spec: spec.version, versions),
            key=lambda spec: cast(int, spec.version),
            default=None,
        )
        if (
            latest_spec
            and latest_spec.version is not None
            and spec.version is not None
            and spec.version < latest_spec.version
        ):
            logger.warn(
                f"The environment {spec.id} is out of date. You should consider "
                f"upgrading to version `v{latest_spec.version}` "
                f"with the environment ID `{latest_spec.id}`."
            )

        env = spec.make(**kwargs)
        return env

    def all(self):
        return self.env_specs.values()

    def spec(self, path: str) -> EnvSpec:
        if ":" in path:
            mod_name, _, id = path.partition(":")
            try:
                importlib.import_module(mod_name)
            except ModuleNotFoundError:
                raise error.Error(
                    f"A module ({mod_name}) was specified for the environment but was not found, "
                    "make sure the package is installed with `pip install` before calling `gym.make()`"
                )
        else:
            id = path

        # We can go ahead and return the env_spec.
        # The EnvSpecTree will take care of any exceptions.
        return self.env_specs[id]

    def register(self, id: str, **kwargs) -> None:
        spec = EnvSpec(id, **kwargs)

        if self._ns is not None:
            if spec.namespace is not None:
                logger.warn(
                    f"Custom namespace '{spec.namespace}' is being overridden by namespace '{self._ns}'. "
                    "If you are developing a plugin you shouldn't specify a namespace in `register` calls. "
                    "The namespace is specified through the entry point key."
                )
            # Replace namespace
            spec.namespace = self._ns

        if spec.id in self.env_specs:
            logger.warn(f"Overriding environment {id}")
        self.env_specs[spec.id] = spec

    @contextlib.contextmanager
    def namespace(self, ns: str):
        self._ns = ns
        yield
        self._ns = None

    def __repr__(self):
        return repr(self.env_specs)


# Have a global registry
registry = EnvRegistry()


def register(id: str, **kwargs) -> None:
    return registry.register(id, **kwargs)


def make(id: str, **kwargs) -> Env:
    return registry.make(id, **kwargs)


def spec(id: str) -> EnvSpec:
    return registry.spec(id)


@contextlib.contextmanager
def namespace(ns: str):
    with registry.namespace(ns):
        yield


def load_env_plugins(entry_point: str = "gym.envs") -> None:
    # Load third-party environments
    for plugin in metadata.entry_points(group=entry_point):
        # Python 3.8 doesn't support plugin.module, plugin.attr
        # So we'll have to try and parse this ourselves
        try:
            module, attr = plugin.module, plugin.attr  # type: ignore  ## error: Cannot access member "attr" for type "EntryPoint"
        except AttributeError:
            if ":" in plugin.value:
                module, attr = plugin.value.split(":", maxsplit=1)
            else:
                module, attr = plugin.value, None
        except:
            module, attr = None, None
        finally:
            if attr is None:
                raise error.Error(
                    f"Gym environment plugin `{module}` must specify a function to execute, not a root module"
                )

        context = namespace(plugin.name)
        if plugin.name.startswith("__") and plugin.name.endswith("__"):
            # `__internal__` is an artifact of the plugin system when
            # the root namespace had an allow-list. The allow-list is now
            # removed and plugins can register environments in the root
            # namespace with the `__root__` magic key.
            if plugin.name == "__root__" or plugin.name == "__internal__":
                context = contextlib.nullcontext()
            else:
                logger.warn(
                    f"The environment namespace magic key `{plugin.name}` is unsupported. "
                    "To register an environment at the root namespace you should specify "
                    "the `__root__` namespace."
                )

        with context:
            fn = plugin.load()
            try:
                fn()
            except Exception as e:
                logger.warn(str(e))
