#!/usr/bin/env python3
"""Enforce the dataclass-domain / Pydantic-boundary architecture rule."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_PRODUCTION_ROOTS = ("ontolib/src", "backend/src", "scripts")
_IGNORED_PARTS = {"__pycache__", ".git", ".venv", "node_modules", "tests"}
_BUILTINS = {
    "Any",
    "Awaitable",
    "Callable",
    "ClassVar",
    "Collection",
    "ConfigDict",
    "Exception",
    "Final",
    "Iterable",
    "Iterator",
    "Literal",
    "Mapping",
    "Never",
    "None",
    "Optional",
    "Path",
    "Protocol",
    "Self",
    "Sequence",
    "TypeAlias",
    "TypeVar",
    "UUID",
    "Warning",
    "Annotated",
    "bool",
    "bytes",
    "date",
    "datetime",
    "dict",
    "float",
    "frozenset",
    "int",
    "list",
    "object",
    "set",
    "str",
    "tuple",
    "type",
}
_BOUNDARY_SUFFIXES = (
    "Manifest",
    "Report",
    "Document",
    "Metadata",
    "DTO",
    "Request",
    "Response",
    "Snapshot",
)

type Symbol = tuple[str, str]


class ClassInfo:
    def __init__(
        self,
        *,
        symbol: Symbol,
        path: Path,
        line: int,
        node: ast.ClassDef,
        bases: tuple[ast.expr, ...],
        fields: dict[str, ast.expr],
        type_parameters: frozenset[str],
    ) -> None:
        self.symbol = symbol
        self.path = path
        self.line = line
        self.node = node
        self.bases = bases
        self.fields = fields
        self.type_parameters = type_parameters
        self.dataclass = False
        self.frozen = False
        self.pydantic_dataclass = False
        self.pydantic = False


class ModuleInfo:
    def __init__(self, *, name: str, path: Path, tree: ast.Module) -> None:
        self.name = name
        self.path = path
        self.tree = tree
        self.imports: dict[str, Symbol] = {}
        self.module_aliases: dict[str, str] = {}
        self.aliases: dict[str, ast.expr] = {}
        self.classes: dict[str, ClassInfo] = {}


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    for prefix in (("ontolib", "src"), ("backend", "src")):
        if tuple(parts[:2]) == prefix:
            parts = parts[2:]
            break
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _source_files(root: Path) -> tuple[Path, ...]:
    if any((root / item).exists() for item in _PRODUCTION_ROOTS):
        candidates = (
            path
            for relative in _PRODUCTION_ROOTS
            for path in (root / relative).rglob("*.py")
        )
    else:
        candidates = root.rglob("*.py")
    return tuple(
        sorted(
            path
            for path in candidates
            if not set(path.relative_to(root).parts) & _IGNORED_PARTS
        )
    )


def _relative_module(module: str, level: int, imported: str | None) -> str:
    parts = module.split(".")
    base = parts[: max(0, len(parts) - level)]
    if imported:
        base.extend(imported.split("."))
    return ".".join(base)


def _collect_module(root: Path, path: Path) -> ModuleInfo:
    name = _module_name(root, path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    info = ModuleInfo(name=name, path=path, tree=tree)
    for node in _module_statements(tree.body):
        _collect_top_level_node(info, node)
    return info


def _module_statements(nodes: list[ast.stmt]) -> list[ast.stmt]:
    result: list[ast.stmt] = []
    for node in nodes:
        result.append(node)
        if isinstance(node, ast.If):
            result.extend(_module_statements(node.body))
            result.extend(_module_statements(node.orelse))
        elif isinstance(node, ast.Try):
            result.extend(_module_statements(node.body))
            result.extend(_module_statements(node.orelse))
            result.extend(_module_statements(node.finalbody))
            for handler in node.handlers:
                result.extend(_module_statements(handler.body))
    return result


def _collect_top_level_node(info: ModuleInfo, node: ast.stmt) -> None:
    if _collect_import(info, node) or _collect_alias(info, node):
        return
    if not isinstance(node, ast.ClassDef):
        return
    fields = {
        item.target.id: item.annotation
        for item in node.body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and not _is_classvar(item.annotation)
    }
    info.classes[node.name] = ClassInfo(
        symbol=(info.name, node.name),
        path=info.path,
        line=node.lineno,
        node=node,
        bases=tuple(node.bases),
        fields=fields,
        type_parameters=frozenset(
            parameter.name
            for parameter in node.type_params
            if isinstance(parameter, (ast.TypeVar, ast.ParamSpec, ast.TypeVarTuple))
        ),
    )


def _collect_import(info: ModuleInfo, node: ast.stmt) -> bool:
    if isinstance(node, ast.Import):
        for item in node.names:
            info.module_aliases.setdefault(item.asname or item.name, item.name)
        return True
    if not isinstance(node, ast.ImportFrom):
        return False
    source = (
        _relative_module(info.name, node.level, node.module)
        if node.level
        else (node.module or "")
    )
    for item in node.names:
        info.imports.setdefault(item.asname or item.name, (source, item.name))
    return True


def _collect_alias(info: ModuleInfo, node: ast.stmt) -> bool:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            info.aliases[target.id] = node.value
            return True
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
        and _annotation_name(node.annotation)
        in {
            "TypeAlias",
            "typing.TypeAlias",
        }
    ):
        info.aliases[node.target.id] = node.value
        return True
    if isinstance(node, ast.TypeAlias) and isinstance(node.name, ast.Name):
        info.aliases[node.name.id] = node.value
        return True
    return False


def _annotation_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _annotation_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _is_classvar(node: ast.expr) -> bool:
    return isinstance(node, ast.Subscript) and _annotation_name(node.value) in {
        "ClassVar",
        "typing.ClassVar",
    }


def _resolve_name(module: ModuleInfo, name: str) -> Symbol | None:
    if name in module.classes or name in module.aliases:
        return module.name, name
    return module.imports.get(name)


def _decorator_symbol(module: ModuleInfo, decorator: ast.expr) -> Symbol | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return _resolve_name(module, target.id)
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        imported = module.module_aliases.get(target.value.id)
        if imported:
            return imported, target.attr
    return None


def _decorator_frozen(decorator: ast.expr) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    return any(
        keyword.arg == "frozen"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in decorator.keywords
    )


def _classify_classes(modules: dict[str, ModuleInfo]) -> dict[Symbol, ClassInfo]:
    classes = {
        item.symbol: item
        for module in modules.values()
        for item in module.classes.values()
    }
    for module in modules.values():
        for item in module.classes.values():
            _classify_decorators(module, item)
    changed = True
    while changed:
        changed = False
        for module in modules.values():
            for item in module.classes.values():
                if _inherits_pydantic(module, item, classes) and not item.pydantic:
                    item.pydantic = True
                    changed = True
    return classes


def _classify_decorators(module: ModuleInfo, item: ClassInfo) -> None:
    for decorator in item.node.decorator_list:
        symbol = _decorator_symbol(module, decorator)
        if symbol == ("dataclasses", "dataclass"):
            item.dataclass = True
            item.frozen = _decorator_frozen(decorator)
        if symbol == ("pydantic.dataclasses", "dataclass"):
            item.pydantic_dataclass = True


def _inherits_pydantic(
    module: ModuleInfo,
    item: ClassInfo,
    classes: dict[Symbol, ClassInfo],
) -> bool:
    for base in item.bases:
        symbol = _resolve_expr_symbol(module, base)
        parent = classes.get(symbol) if symbol else None
        if symbol in {
            ("pydantic", "BaseModel"),
            ("pydantic_settings", "BaseSettings"),
        } or bool(parent and parent.pydantic):
            return True
    return False


def _resolve_expr_symbol(module: ModuleInfo, node: ast.expr) -> Symbol | None:
    if isinstance(node, ast.Name):
        return _resolve_name(module, node.id)
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        imported = module.module_aliases.get(node.value.id)
        if imported:
            return imported, node.attr
    return None


def _annotation_symbols(
    module: ModuleInfo,
    node: ast.expr,
    modules: dict[str, ModuleInfo],
    *,
    seen: frozenset[Symbol] = frozenset(),
) -> tuple[set[Symbol], set[str]]:
    special = _special_annotation_symbols(module, node, modules, seen)
    if special is not None:
        return special
    symbols: set[Symbol] = set()
    unresolved: set[str] = set()
    if isinstance(node, ast.Name):
        return _name_annotation_symbols(module, node.id, modules, seen)
    elif isinstance(node, ast.Attribute):
        symbol = _resolve_expr_symbol(module, node)
        if symbol:
            symbols.add(symbol)
        elif name := _annotation_name(node):
            unresolved.add(name)
    else:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                found, missing = _annotation_symbols(module, child, modules, seen=seen)
                symbols.update(found)
                unresolved.update(missing)
    return symbols, unresolved


def _special_annotation_symbols(
    module: ModuleInfo,
    node: ast.expr,
    modules: dict[str, ModuleInfo],
    seen: frozenset[Symbol],
) -> tuple[set[Symbol], set[str]] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            parsed = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return set(), {node.value}
        return _annotation_symbols(module, parsed, modules, seen=seen)
    if not isinstance(node, ast.Subscript):
        return None
    container = _annotation_name(node.value)
    if container in {"Literal", "typing.Literal"}:
        return set(), set()
    if container not in {"Annotated", "typing.Annotated"}:
        return None
    first = node.slice.elts[0] if isinstance(node.slice, ast.Tuple) else node.slice
    return _annotation_symbols(module, first, modules, seen=seen)


def _name_annotation_symbols(
    module: ModuleInfo,
    name: str,
    modules: dict[str, ModuleInfo],
    seen: frozenset[Symbol],
) -> tuple[set[Symbol], set[str]]:
    symbol = _resolve_name(module, name)
    if symbol is None:
        if name in _BUILTINS or name in module.aliases:
            return set(), set()
        return set(), {name}
    alias_module = modules.get(symbol[0])
    if alias_module and symbol[1] in alias_module.aliases and symbol not in seen:
        alias = alias_module.aliases[symbol[1]]
        if isinstance(alias, ast.Call) and _annotation_name(alias.func) in {
            "TypeVar",
            "typing.TypeVar",
        }:
            return set(), set()
        return _annotation_symbols(
            alias_module,
            alias,
            modules,
            seen=seen | {symbol},
        )
    return {symbol}, set()


def _inherited_fields(
    item: ClassInfo,
    module: ModuleInfo,
    modules: dict[str, ModuleInfo],
    classes: dict[Symbol, ClassInfo],
    seen: frozenset[Symbol] = frozenset(),
) -> dict[str, tuple[ModuleInfo, ast.expr]]:
    if item.symbol in seen:
        return {}
    result: dict[str, tuple[ModuleInfo, ast.expr]] = {}
    for base in item.bases:
        symbol = _resolve_expr_symbol(module, base)
        parent = classes.get(symbol) if symbol else None
        parent_module = modules.get(symbol[0]) if symbol else None
        if parent and parent_module:
            result.update(
                _inherited_fields(
                    parent,
                    parent_module,
                    modules,
                    classes,
                    seen | {item.symbol},
                )
            )
            result.update(
                {name: (parent_module, value) for name, value in parent.fields.items()}
            )
    result.update({name: (module, value) for name, value in item.fields.items()})
    return result


def _cross_findings(
    root: Path,
    modules: dict[str, ModuleInfo],
    classes: dict[Symbol, ClassInfo],
) -> list[str]:
    findings: list[str] = []
    for module in modules.values():
        for item in module.classes.values():
            if not (item.dataclass or item.pydantic):
                continue
            fields = _inherited_fields(item, module, modules, classes)
            for name, (field_module, annotation) in fields.items():
                symbols, unresolved = _annotation_symbols(
                    field_module, annotation, modules
                )
                unresolved.difference_update(item.type_parameters)
                targets = [classes[symbol] for symbol in symbols if symbol in classes]
                crosses = (
                    item.dataclass and any(target.pydantic for target in targets)
                ) or (item.pydantic and any(target.dataclass for target in targets))
                location = item.path.relative_to(root)
                if crosses:
                    direction = (
                        "dataclass contains Pydantic model"
                        if item.dataclass
                        else "Pydantic model contains dataclass"
                    )
                    findings.append(
                        f"{location}:{item.line} {item.symbol[1]}.{name}: {direction}"
                    )
                for missing in sorted(unresolved):
                    findings.append(
                        f"{location}:{item.line} {item.symbol[1]}.{name}: "
                        f"unresolved project annotation {missing}"
                    )
    return findings


def _mutable_and_hybrid_findings(
    root: Path, classes: dict[Symbol, ClassInfo]
) -> list[str]:
    findings: list[str] = []
    for item in classes.values():
        location = item.path.relative_to(root)
        if item.dataclass and not item.frozen:
            findings.append(
                f"{location}:{item.line} {item.symbol[1]}: dataclass must be frozen"
            )
        if item.pydantic_dataclass:
            findings.append(
                f"{location}:{item.line} {item.symbol[1]}: "
                "Pydantic dataclass is forbidden"
            )
        if item.dataclass and item.symbol[1].endswith(_BOUNDARY_SUFFIXES):
            findings.append(
                f"{location}:{item.line} {item.symbol[1]}: "
                "serialized boundary document must use strict Pydantic"
            )
    return findings


def _config_dict_values(node: ast.expr) -> dict[str, object]:
    if not isinstance(node, ast.Call) or _annotation_name(node.func) not in {
        "ConfigDict",
        "pydantic.ConfigDict",
        "SettingsConfigDict",
        "pydantic_settings.SettingsConfigDict",
    }:
        return {}
    return {
        keyword.arg: keyword.value.value
        for keyword in node.keywords
        if keyword.arg is not None and isinstance(keyword.value, ast.Constant)
    }


def _effective_pydantic_config(
    item: ClassInfo,
    module: ModuleInfo,
    modules: dict[str, ModuleInfo],
    classes: dict[Symbol, ClassInfo],
    seen: frozenset[Symbol] = frozenset(),
) -> dict[str, object]:
    if item.symbol in seen:
        return {}
    config: dict[str, object] = {}
    for base in item.bases:
        symbol = _resolve_expr_symbol(module, base)
        parent = classes.get(symbol) if symbol else None
        parent_module = modules.get(symbol[0]) if symbol else None
        if parent and parent_module:
            config.update(
                _effective_pydantic_config(
                    parent,
                    parent_module,
                    modules,
                    classes,
                    seen | {item.symbol},
                )
            )
    for node in item.node.body:
        if (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and (
                target := (
                    node.targets[0]
                    if isinstance(node, ast.Assign) and len(node.targets) == 1
                    else node.target
                    if isinstance(node, ast.AnnAssign)
                    else None
                )
            )
            and isinstance(target, ast.Name)
            and target.id == "model_config"
            and (value := node.value) is not None
        ):
            config.update(_config_dict_values(value))
    return config


def _pydantic_config_findings(
    root: Path,
    modules: dict[str, ModuleInfo],
    classes: dict[Symbol, ClassInfo],
) -> list[str]:
    findings: list[str] = []
    for module in modules.values():
        for item in module.classes.values():
            if not item.pydantic:
                continue
            config = _effective_pydantic_config(item, module, modules, classes)
            settings = _inherits_symbol(
                module,
                item,
                classes,
                ("pydantic_settings", "BaseSettings"),
            )
            expected_config = (
                (("strict", True),)
                if settings
                else (
                    ("strict", True),
                    ("extra", "forbid"),
                )
            )
            missing = [
                f"{key}={expected!r}"
                for key, expected in expected_config
                if config.get(key) != expected
            ]
            if missing:
                findings.append(
                    f"{item.path.relative_to(root)}:{item.line} {item.symbol[1]}: "
                    f"Pydantic boundary model requires {' and '.join(missing)}"
                )
    return findings


def _inherits_symbol(
    module: ModuleInfo,
    item: ClassInfo,
    classes: dict[Symbol, ClassInfo],
    target: Symbol,
    seen: frozenset[Symbol] = frozenset(),
) -> bool:
    if item.symbol in seen:
        return False
    for base in item.bases:
        symbol = _resolve_expr_symbol(module, base)
        if symbol == target:
            return True
        parent = classes.get(symbol) if symbol else None
        if parent and _inherits_symbol(
            module,
            parent,
            classes,
            target,
            seen | {item.symbol},
        ):
            return True
    return False


def _asdict_findings(
    root: Path,
    modules: dict[str, ModuleInfo],
    classes: dict[Symbol, ClassInfo],
) -> list[str]:
    findings: list[str] = []
    for module in modules.values():
        asdict_names = {
            name
            for name, symbol in module.imports.items()
            if symbol == ("dataclasses", "asdict")
        }
        dataclasses_aliases = {
            name
            for name, imported in module.module_aliases.items()
            if imported == "dataclasses"
        }
        for parent in ast.walk(module.tree):
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                findings.extend(
                    _function_asdict_findings(
                        root,
                        module,
                        parent,
                        classes,
                        asdict_names,
                        dataclasses_aliases,
                    )
                )
    return findings


def _function_asdict_findings(
    root: Path,
    module: ModuleInfo,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    classes: dict[Symbol, ClassInfo],
    asdict_names: set[str],
    dataclasses_aliases: set[str],
) -> list[str]:
    parameter_types = {
        argument.arg: symbol
        for argument in (*function.args.posonlyargs, *function.args.args)
        if argument.annotation is not None
        and (symbol := _resolve_expr_symbol(module, argument.annotation)) is not None
    }
    owner = next(
        (item for item in module.classes.values() if function in item.node.body),
        None,
    )
    findings: list[str] = []
    for call in ast.walk(function):
        if not isinstance(call, ast.Call) or not _is_asdict_call(
            call, asdict_names, dataclasses_aliases
        ):
            continue
        argument = call.args[0]
        symbol = _asdict_argument_symbol(argument, owner, parameter_types)
        target = classes.get(symbol) if symbol else None
        if target and target.dataclass:
            findings.append(
                f"{module.path.relative_to(root)}:{call.lineno} "
                f"{target.symbol[1]}: direct dataclasses.asdict "
                "boundary serialization"
            )
    return findings


def _is_asdict_call(
    node: ast.Call,
    direct_names: set[str],
    module_names: set[str],
) -> bool:
    if not node.args:
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in direct_names
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "asdict"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in module_names
    )


def _asdict_argument_symbol(
    argument: ast.expr,
    owner: ClassInfo | None,
    parameter_types: dict[str, Symbol],
) -> Symbol | None:
    if not isinstance(argument, ast.Name):
        return None
    if owner and argument.id == "self":
        return owner.symbol
    return parameter_types.get(argument.id)


def validate_model_boundaries(root: Path) -> list[str]:
    """Return deterministic architecture violations without importing source code."""
    modules = {
        info.name: info
        for path in _source_files(root)
        if (info := _collect_module(root, path))
    }
    classes = _classify_classes(modules)
    findings = [
        *_cross_findings(root, modules, classes),
        *_mutable_and_hybrid_findings(root, classes),
        *_asdict_findings(root, modules, classes),
        *_pydantic_config_findings(root, modules, classes),
    ]
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args(argv)
    findings = validate_model_boundaries(args.root.resolve())
    if findings:
        print("Model boundary violations:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    print("Dataclass/Pydantic model boundaries are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
