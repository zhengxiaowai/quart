import re
import uuid
from ast import literal_eval
from collections import defaultdict
from typing import Any, Dict, Generator, List, NamedTuple, Optional, Set, Tuple, Union  # noqa
from typing.re import Pattern  # noqa

from sortedcontainers import SortedListWithKey

from .exceptions import MethodNotAllowed, NotFound, RedirectRequired


ROUTE_VAR_RE = re.compile(r'''  # noqa
    (?P<static>[^<]*)                           # static rule data
    <
    (?:
        (?P<converter>[a-zA-Z_][a-zA-Z0-9_]*)   # converter name
        (?:\((?P<args>.*?)\))?                  # converter arguments
        \:                                      # variable delimiter
    )?
    (?P<variable>[a-zA-Z][a-zA-Z0-9_]*)         # variable name
    >
''', re.VERBOSE)  # noqa

CONVERTER_ARGS_RE = re.compile(r'''  # noqa
    ((?P<name>\w+)\s*=\s*)?
    (?P<value>
        True|False|
        \d+.\d+|
        \d+.|
        \d+|
        \w+|
        [urUR]?(?P<str_value>"[^"]*?"|'[^']*')
    )\s*,
''', re.VERBOSE | re.UNICODE)  # noqa

VariablePart = NamedTuple(
    'VariablePart',
    [('converter', Optional[str]), ('arguments', Tuple[List[Any], Dict[str, Any]]), ('name', str)],
)
WeightedPart = NamedTuple('Weight', [('converter', bool), ('weight', int)])


class ValidationError(Exception):
    pass


class BuildError(Exception):
    pass


class BaseConverter:
    regex = r'[^/]+'
    weight = 100

    def to_python(self, value: str) -> Any:
        return value

    def to_url(self, value: Any) -> str:
        return value


class StringConverter(BaseConverter):

    def __init__(
            self, minlength: int=1, maxlength: Optional[int]=None, length: Optional[int]=None,
    ) -> None:
        if length is not None:
            re_length = '{%d}' % length
        else:
            maxlength = '' if maxlength is None else int(maxlength)  # type: ignore
            re_length = '{%d,%s}' % (minlength, maxlength)
        self.regex = f"[^/]{re_length}"


class AnyConverter(BaseConverter):
    def __init__(self, *items: str) -> None:
        self.regex = '(?:%s)' % '|'.join((re.escape(x) for x in items))


class PathConverter(BaseConverter):
    regex = r'[^/].*?'
    weight = 200


class IntegerConverter(BaseConverter):
    regex = r'\d+'
    weight = 50

    def __init__(
            self, fixed_digits: Optional[int]=None, min: Optional[int]=None,
            max: Optional[int]=None,
    ) -> None:
        self.fixed_digits = fixed_digits
        self.min = min
        self.max = max

    def to_python(self, value: str) -> int:
        if self.fixed_digits is not None and len(value) > self.fixed_digits:
            raise ValidationError()
        converted_value = int(value)
        if (
                self.min is not None and self.min > converted_value or
                self.max is not None and self.max < converted_value
        ):
            raise ValidationError()
        return converted_value

    def to_url(self, value: int) -> str:
        if self.fixed_digits is not None:
            return f"{value:0{self.fixed_digits}d}"  # type: ignore
        else:
            return str(value)


class FloatConverter(BaseConverter):
    regex = r'\d+\.\d+'
    weight = 50

    def __init__(self, min: Optional[float]=None, max: Optional[float]=None) -> None:
        self.min = min
        self.max = max

    def to_python(self, value: str) -> float:
        converted_value = float(value)
        if (
                self.min is not None and self.min > converted_value or
                self.max is not None and self.max < converted_value
        ):
            raise ValidationError()
        return converted_value

    def to_url(self, value: float) -> str:
        return str(value)


class UUIDConverter(BaseConverter):
    regex = r'[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}'  # noqa

    def to_python(self, value: str) -> uuid.UUID:
        return uuid.UUID(value)

    def to_url(self, value: uuid.UUID) -> str:
        return str(value)


class Map:

    default_converters = {
        'any': AnyConverter,
        'default': StringConverter,
        'float': FloatConverter,
        'int': IntegerConverter,
        'path': PathConverter,
        'string': StringConverter,
        'uuid': UUIDConverter,
    }

    def __init__(self) -> None:
        self.rules = SortedListWithKey(key=lambda rule: rule.match_key)
        self.endpoints: Dict[str, SortedListWithKey] = defaultdict(lambda: SortedListWithKey(key=lambda rule: rule.build_key))  # noqa
        self.converters = self.default_converters.copy()

    def add(self, rule: 'Rule') -> None:
        rule.bind(self)
        self.endpoints[rule.endpoint].add(rule)
        self.rules.add(rule)

    def bind_to_request(
            self,
            scheme: str,
            server_name: str,
            method: str,
            path: str,
    ) -> 'MapAdapter':
        return MapAdapter(self, scheme, server_name, method, path)

    def bind(self, scheme: str, server_name: str) -> 'MapAdapter':
        return MapAdapter(self, scheme, server_name)


class MapAdapter:

    def __init__(
            self,
            map: Map,
            scheme: str,
            server_name: str,
            method: Optional[str]=None,
            path: Optional[str]=None,
    ) -> None:
        self.map = map
        self.scheme = scheme
        self.server_name = server_name
        self.path = path
        self.method = method

    def build(
            self,
            endpoint: str,
            values: Optional[dict]=None,
            method: Optional[str]=None,
            scheme: Optional[str]=None,
            external: bool=False,
    )-> str:
        values = values or {}
        for rule in self.map.endpoints[endpoint]:
            if rule.buildable(values, method=method):
                path = rule.build(**values)
                if external:
                    scheme = scheme or self.scheme
                    return f"{scheme}://{self.server_name}{path}"
                else:
                    return path
        raise BuildError()

    def match(self) -> Tuple['Rule', Dict[str, Any]]:
        allowed_methods: Set[str] = set()
        for rule, variables, needs_slash in self._matches():
            if self.method in rule.methods:
                if needs_slash:
                    raise RedirectRequired(rule.build(**variables))
                else:
                    return rule, variables
            else:
                allowed_methods.update(rule.methods)
        if allowed_methods:
            raise MethodNotAllowed(allowed_methods=allowed_methods)
        raise NotFound()

    def allowed_methods(self) -> Set[str]:
        allowed_methods: Set[str] = set()
        for rule, *_ in self._matches():
            allowed_methods.update(rule.methods)
        return allowed_methods

    def _matches(self) -> Generator[Tuple['Rule', Dict[str, Any], bool], None, None]:
        for rule in self.map.rules:
            variables, needs_slash = rule.match(self.path)
            if variables is not None:
                yield rule, variables, needs_slash


class Rule:

    def __init__(
            self, rule: str, methods: List[str], endpoint: str, strict_slashes: bool=True,
            *, provide_automatic_options: bool=True
    ) -> None:
        if not rule.startswith('/'):
            raise ValueError(f"Rule '{rule}' does not start with a slash")
        self.rule = rule
        self.is_leaf = not rule.endswith('/')
        if 'GET' in methods and 'HEAD' not in methods:
            methods.append('HEAD')
        self.methods = frozenset(method.upper() for method in methods)
        self.endpoint = endpoint
        self.strict_slashes = strict_slashes
        self.map: Optional[Map] = None
        self._pattern: Optional[Pattern] = None
        self._builder: Optional[str] = None
        self._converters: Dict[str, BaseConverter] = {}
        self._weights: List[WeightedPart] = []
        self.provide_automatic_options = provide_automatic_options

    def __repr__(self) -> str:
        return f"Rule({self.rule}, {self.methods}, {self.endpoint}, {self.strict_slashes})"

    def match(self, path: str) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Check if the path matches this Rule.

        If it does it returns a dict of matched and converted values,
        otherwise None is returned.
        """
        match = self._pattern.match(path)
        if match is not None:
            # If the route is a branch (not leaf) and the path is
            # missing a trailing slash then it needs one to be
            # considered a match in the strict slashes mode.
            needs_slash = (
                self.strict_slashes and not self.is_leaf and match.groupdict()['__slash__'] != '/'
            )
            try:
                converted_varaibles = {
                    name: self._converters[name].to_python(value)
                    for name, value in match.groupdict().items()
                    if name != '__slash__'
                }
            except ValidationError:  # Doesn't meet conversion rules, no match
                return None, False
            else:
                return converted_varaibles, needs_slash
        else:
            return None, False

    def build(self, **values: Any) -> str:
        """Build this rule into a path using the values given."""
        converted_values = {
            key: self._converters[key].to_url(value) for key, value in values.items()
        }
        return self._builder.format(**converted_values)

    def buildable(self, values: Optional[dict]=None, method: Optional[str]=None) -> bool:
        if method is not None and method not in self.methods:
            return False
        return set(values.keys()) <= set(self._converters.keys())

    def bind(self, map: Map) -> None:
        """Bind the Rule to a Map and compile it."""
        if self.map is not None:
            raise RuntimeError(f"{self!r} is already bound to {self.map!r}")

        self.map = map

        pattern = ''
        builder = ''
        for part in _parse_rule(self.rule):
            if isinstance(part, VariablePart):
                converter = self.map.converters[part.converter](
                    *part.arguments[0], **part.arguments[1],
                )
                pattern += f"(?P<{part.name}>{converter.regex})"
                self._converters[part.name] = converter
                builder += '{' + part.name + '}'
                self._weights.append(WeightedPart(True, converter.weight))
            else:
                builder += part
                pattern += part
                self._weights.append(WeightedPart(False, -len(part)))
        if not self.is_leaf and self.strict_slashes:
            # Pattern should match with or without a trailing slash
            pattern = f"{pattern.rstrip('/')}(?<!/)(?P<__slash__>/?)$"
        else:
            pattern = f"{pattern}$"
        self._pattern = re.compile(pattern)
        self._builder = builder

    @property
    def match_key(self) -> Tuple[bool, int, List[WeightedPart]]:
        """A Key to sort the rules by weight for matching.

        The key leads to ordering:

         - By first order on the complexity of the rule, i.e. does it
           have any converted parts. This is as simple rules are quick
           to match or reject.
         - Then by the number of parts, with more complex (more parts)
           first.
         - Finally by the weights themselves. Note that weights are also
           sub keyed by converter first then weight second.
        """
        if self.map is None:
            raise RuntimeError(f"{self!r} is not bound to a Map")
        complex_rule = any(weight.converter for weight in self._weights)
        return (complex_rule, -len(self._weights), self._weights)

    @property
    def build_key(self) -> int:
        """A Key to sort the rules by weight for building.

        The more complex routes (most converted parts) should be first.
        """
        if self.map is None:
            raise RuntimeError(f"{self!r} is not bound to a Map")
        return (-sum(1 for weight in self._weights if weight.converter))


def _parse_rule(rule: str) -> Generator[Union[str, VariablePart], None, None]:
    variable_names: Set[str] = set()
    final_match = 0
    for match in ROUTE_VAR_RE.finditer(rule):
        named_groups = match.groupdict()
        if named_groups['static'] is not None:
            yield named_groups['static']
        variable = named_groups['variable']
        if variable in variable_names:
            raise ValueError(f"Variable name {variable} used more than once")
        else:
            variable_names.add(variable)
        arguments = _parse_converter_args(named_groups['args'] or '')
        yield VariablePart(named_groups['converter'] or 'default', arguments, variable)  # type: ignore  # noqa
        final_match = match.span()[-1]
    yield rule[final_match:]


def _parse_converter_args(raw: str) -> Tuple[List[Any], Dict[str, Any]]:
    raw += ','  # Simplifies matching regex if each argument has a trailing comma
    args = []
    kwargs = {}
    for match in CONVERTER_ARGS_RE.finditer(raw):
        value = match.group('str_value') or match.group('value')
        try:
            value = literal_eval(value)
        except ValueError:
            value = str(value)
        name = match.group('name')
        if not name:
            args.append(value)
        else:
            kwargs[name] = value

    return args, kwargs
