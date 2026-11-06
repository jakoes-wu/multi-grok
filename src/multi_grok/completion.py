"""`multi-grok completion SHELL`：生成 bash / zsh / fish 补全脚本（方案 v0.2 §5.1.5；
取值补全见 docs/feature/feature-completion-values.md）。

子命令与选项从 argparse 解析器遍历得到，避免手写清单与实际参数漂移。遍历用到
`parser._actions` 与 `argparse._SubParsersAction.choices` 两个未写入文档的属性，
兼容性由测试在 Python 3.8 与 3.12 上兜底。账号名在补全时调用 `multi-grok list --names` 获取。
本模块只输出脚本文本，不修改任何 shell 配置文件。
"""

import argparse
import shlex
from typing import Dict, List, NamedTuple, Tuple

SHELLS = ("bash", "zsh", "fish")

# 各子命令第 N 个位置参数的候选类型：name 账号名、dir 目录、file 文件、shell 补全的 shell 名、
# proxy 代理固定值、statusline 该子命令的动作、clash 是 clash-api 的固定词（也可写地址）。
POSITIONAL_KINDS = {
    "add": ["name"], "set": ["name"], "proxy": ["name", "proxy"], "env": ["name"],
    "args": ["name"], "remove": ["name"], "usage": ["name"], "completion": ["shell"],
    "route": ["dir", "name"], "which": ["dir"], "rename": ["name", "name"], "mcp": ["name"],
    "handoff": ["name"], "login": ["name"], "run": ["name"], "path": ["name"],
    "code": ["name", "file"], "export": ["name", "file"], "import": ["file", "name"],
    "sessions": ["name"], "netcheck": ["name"], "clash-api": ["clash"],
}
# 选项值是路径、应按文件补全的选项。
PATH_OPTIONS = ("-f", "--file", "--root", "--bin-dir", "--shared-dir")
# 选项值是账号名的选项。
NAME_OPTIONS = ("--default", "--from")
FIXED_WORDS = {"shell": SHELLS, "proxy": ("off", "inherit"), "clash": ("auto", "off")}
# 选项值是共享项名的选项：补全时调用 `multi-grok completion --list-shared-items` 读当前配置。
SHARED_OPTIONS = ("--shared-exclude", "--shared-include")
SHARED_SOURCE = "multi-grok completion --list-shared-items 2>/dev/null"

# value_spec 的两张表：{(子命令, 位置序号): [词]}、{(子命令, 选项写法): [词]}。
PositionalValues = Dict[Tuple[str, int], List[str]]
OptionValues = Dict[Tuple[str, str], List[str]]


def _positional_cases(indent: str) -> str:
    """生成 `case "$cmd:$npos" in` 的分支，按候选类型合并。"""
    by_kind: Dict[str, List[str]] = {}
    for command, kinds in sorted(POSITIONAL_KINDS.items()):
        for index, kind in enumerate(kinds):
            by_kind.setdefault(kind, []).append("{}:{}".format(command, index))
    return "\n".join("{}{}) kind={} ;;".format(indent, "|".join(keys), kind) for kind, keys in sorted(by_kind.items()))


class CommandSpec(NamedTuple):
    options: List[str]
    # 需要跟一个值的选项（补全时要跳过它的值，才能数对位置参数）
    value_options: List[str]


def collect(parser: argparse.ArgumentParser) -> Dict[str, CommandSpec]:
    """返回 {子命令: CommandSpec}；键 "" 表示顶层（尚未输入子命令时）。"""
    specs = {"": CommandSpec(_options(parser), [])}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                specs[name] = CommandSpec(_options(sub), _value_options(sub))
    return specs


def _options(parser: argparse.ArgumentParser) -> List[str]:
    # 隐藏选项（help=SUPPRESS，如 completion --list-shared-items）只给补全脚本自己调用，不补出来。
    return [option for action in parser._actions if action.help != argparse.SUPPRESS
            for option in action.option_strings]


def _value_options(parser: argparse.ArgumentParser) -> List[str]:
    # nargs == 0 的是开关（store_true / help / version 等），其余选项都要跟值。
    return [option for action in parser._actions
            if action.option_strings and action.nargs != 0 and action.help != argparse.SUPPRESS
            for option in action.option_strings]


def value_spec(parser: argparse.ArgumentParser) -> Tuple[PositionalValues, OptionValues]:
    """从 argparse 的 choices 读出补全取值：返回 (位置参数取值表, 选项取值表)。

    写了 choices 的参数自动读出，新增这类参数不用改本文件。位置参数按出现顺序从 0 编号，与脚本里数 npos 的
    规则一致（不算选项和选项的值）。nargs 为 * / + / 整数的位置参数会吃掉多个词，脚本里的序号从它开始就对不上，
    所以从它起不再编号；它自己带 choices 时直接报错，逼新增参数的人先扩展这里。
    已在 POSITIONAL_KINDS 登记的位置不进取值表（登记的类型优先，如 completion:0 仍按 shell 补）。
    已知限制：nargs="?" 的参数被省略时后面的序号会错位；目前没有“可省略参数后面还跟带取值参数”的子命令。
    """
    positional = {}  # type: PositionalValues
    options = {}  # type: OptionValues
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            index = 0
            counting = True
            for sub_action in sub._actions:
                if sub_action.help == argparse.SUPPRESS:
                    continue
                if not sub_action.option_strings:
                    multi = sub_action.nargs not in (None, "?")
                    if sub_action.choices and multi:
                        raise ValueError("completion values for a multi-value positional: {} {}".format(
                            name, sub_action.dest))
                    if not counting:
                        continue
                    if multi:
                        counting = False
                        continue
                    registered = index < len(POSITIONAL_KINDS.get(name, []))
                    if sub_action.choices and not registered:
                        positional[(name, index)] = [str(choice) for choice in sub_action.choices]
                    index += 1
                    continue
                if sub_action.nargs != 0 and sub_action.choices:
                    for option in sub_action.option_strings:
                        options[(name, option)] = [str(choice) for choice in sub_action.choices]
    return positional, options


def render(shell: str, parser: argparse.ArgumentParser) -> str:
    specs = collect(parser)
    positional, options = value_spec(parser)
    if shell == "bash":
        return _bash(specs, positional, options)
    if shell == "zsh":
        return _zsh(specs, positional, options)
    return _fish(specs, positional, options)


def _shared_keys(specs: Dict[str, CommandSpec]) -> List[str]:
    """`子命令:选项` 形式的共享项选项全集，按子命令排序。"""
    return ["{}:{}".format(name, option) for name, spec in sorted(specs.items()) if name
            for option in spec.value_options if option in SHARED_OPTIONS]


def _value_cases(positional: PositionalValues, options: OptionValues, specs: Dict[str, CommandSpec],
                 indent: str, zsh: bool) -> Tuple[str, str]:
    """生成两段 case 分支：位置参数取值（按 "$cmd:$npos"）与选项取值（按 "$cmd:$prev"）。

    同一组词的键合并成一条分支。bash 的 candidates 是字符串，zsh 是数组。选项取值段末尾保留
    `*)` 分支：没有取值的选项维持原行为（不补）。
    """
    def assign(words: List[str]) -> str:
        return "candidates=({})".format(" ".join(words)) if zsh else 'candidates="{}"'.format(" ".join(words))

    def grouped(table: Dict) -> List[Tuple[List[str], List[str]]]:
        merged = {}  # type: Dict[Tuple[str, ...], List[str]]
        for key in sorted(table, key=lambda item: (item[0], str(item[1]))):
            merged.setdefault(tuple(table[key]), []).append("{}:{}".format(key[0], key[1]))
        return [(keys, list(words)) for words, keys in merged.items()]

    position_lines = ["{}{}) {} ;;".format(indent, "|".join(keys), assign(words))
                      for keys, words in grouped(positional)]
    option_lines = []
    shared = _shared_keys(specs)
    if shared:
        option_lines.append("{}{}) kind=shared ;;".format(indent, "|".join(shared)))
    option_lines.extend("{}{}) {} ;;".format(indent, "|".join(keys), assign(words))
                        for keys, words in grouped(options))
    option_lines.append("{}*) {} ;;".format(indent, "return" if zsh else "COMPREPLY=(); return 0"))
    return "\n".join(position_lines), "\n".join(option_lines)


def _words(items: List[str]) -> str:
    return " ".join(items)


def _bash(specs: Dict[str, CommandSpec], positional: PositionalValues, options: OptionValues) -> str:
    commands = sorted(name for name in specs if name)
    position_values, option_values = _value_cases(positional, options, specs, "          ", zsh=False)
    option_cases = "\n".join("    {}) opts={}; vopts={} ;;".format(
        name, shlex.quote(_words(spec.options)), shlex.quote(_words(spec.value_options)))
        for name, spec in sorted(specs.items()) if name)
    return """# bash completion for multi-grok (generated by `multi-grok completion bash`)
_multi_grok() {
  # COMP_WORDBREAKS 默认含 @ 与 :，COMP_WORDS 会把账号名 a@b 拆开，所以自己从 COMP_LINE 取完整的当前词。
  local line="${COMP_LINE:0:COMP_POINT}"
  local cur="${line##* }"
  local before="${line%%"$cur"}"
  local -a words
  read -r -a words <<< "$before"
  # 第一个不以 - 开头的词是子命令。
  local cmd="" i w
  for ((i = 1; i < ${#words[@]}; i++)); do
    if [[ "${words[i]}" != -* ]]; then cmd="${words[i]}"; break; fi
  done
  local opts="" vopts=""
  case "$cmd" in
%(option_cases)s
  esac
  # 数子命令之后已输入的位置参数个数，跳过选项与它的值；遇到 -- 记为 -1（args 的参数列表不补全）。
  local npos=0 skip=0 prev=""
  for ((i = i + 1; i < ${#words[@]}; i++)); do
    w="${words[i]}"
    if ((skip)); then skip=0; prev=""; continue; fi
    if [[ "$w" == "--" ]]; then npos=-1; break; fi
    if [[ "$w" == -* ]]; then
      if [[ " $vopts " == *" $w "* ]]; then skip=1; prev="$w"; fi
      continue
    fi
    ((npos++))
  done
  local candidates="" kind=""
  if ((skip)); then
    case "$prev" in
      %(path_options)s) COMPREPLY=($(compgen -f -- "$cur")); return 0 ;;
      %(name_options)s) kind=name ;;
      *)
        # 共享项与写了 choices 的选项补出取值；其它选项的值不补。
        case "$cmd:$prev" in
%(option_values)s
        esac ;;
    esac
  elif [[ -z "$cmd" ]]; then
    candidates="%(commands)s --help --version"
  elif ((npos < 0)); then
    COMPREPLY=(); return 0
  elif [[ "$cur" == -* ]]; then
    candidates="$opts"
  else
    case "$cmd:$npos" in
%(positional_cases)s
    esac
    if [[ -z "$kind" ]]; then
      case "$cmd:$npos" in
%(position_values)s
      esac
    fi
  fi
  case "$kind" in
    name) candidates="$(multi-grok list --names 2>/dev/null)" ;;
    dir) COMPREPLY=($(compgen -d -- "$cur")); return 0 ;;
    file) COMPREPLY=($(compgen -f -- "$cur")); return 0 ;;
    shell) candidates="%(shells)s" ;;
    proxy) candidates="off inherit" ;;
    clash) candidates="auto off" ;;
    shared) candidates="$(%(shared_source)s)" ;;
    statusline) candidates="install uninstall" ;;
  esac
  COMPREPLY=($(compgen -W "$candidates" -- "$cur"))
  # bash 只替换当前词最后一个分词字符之后的片段，候选要去掉同样长的前缀。
  local j prefix=""
  for ((j = ${#cur} - 1; j >= 0; j--)); do
    if [[ "$COMP_WORDBREAKS" == *"${cur:j:1}"* ]]; then prefix="${cur:0:j+1}"; break; fi
  done
  if [[ -n "$prefix" ]]; then
    for i in "${!COMPREPLY[@]}"; do COMPREPLY[i]="${COMPREPLY[i]#"$prefix"}"; done
  fi
}
complete -F _multi_grok multi-grok
""" % {"option_cases": option_cases, "commands": _words(commands), "shells": _words(list(SHELLS)),
       "positional_cases": _positional_cases("      "), "path_options": "|".join(PATH_OPTIONS),
       "name_options": "|".join(NAME_OPTIONS), "option_values": option_values,
       "position_values": position_values, "shared_source": SHARED_SOURCE}


def _zsh(specs: Dict[str, CommandSpec], positional: PositionalValues, options: OptionValues) -> str:
    commands = sorted(name for name in specs if name)
    position_values, option_values = _value_cases(positional, options, specs, "          ", zsh=True)
    option_cases = "\n".join("    {}) opts=({}); vopts=({}) ;;".format(
        name, _words([shlex.quote(o) for o in spec.options]),
        _words([shlex.quote(o) for o in spec.value_options]))
        for name, spec in sorted(specs.items()) if name)
    return """#compdef multi-grok
# zsh completion for multi-grok (generated by `multi-grok completion zsh`)
_multi_grok() {
  local cmd="" i w
  local -a opts vopts candidates
  # zsh 的 $words 不按 @ 分词，可直接使用；第一个不以 - 开头的词是子命令。
  for ((i = 2; i < CURRENT; i++)); do
    if [[ "${words[i]}" != -* ]]; then cmd="${words[i]}"; break; fi
  done
  case "$cmd" in
%(option_cases)s
  esac
  local npos=0 skip=0 prev=""
  for ((i = i + 1; i < CURRENT; i++)); do
    w="${words[i]}"
    if ((skip)); then skip=0; prev=""; continue; fi
    if [[ "$w" == "--" ]]; then npos=-1; break; fi
    if [[ "$w" == -* ]]; then
      if (( ${vopts[(Ie)$w]} )); then skip=1; prev="$w"; fi
      continue
    fi
    ((npos++))
  done
  local cur="${words[CURRENT]}" kind=""
  if ((skip)); then
    case "$prev" in
      %(path_options)s) _files; return ;;
      %(name_options)s) kind=name ;;
      *)
        case "$cmd:$prev" in
%(option_values)s
        esac ;;
    esac
  elif [[ -z "$cmd" ]]; then
    candidates=(%(commands)s --help --version)
  elif ((npos < 0)); then
    return
  elif [[ "$cur" == -* ]]; then
    candidates=($opts)
  else
    case "$cmd:$npos" in
%(positional_cases)s
    esac
    if [[ -z "$kind" ]]; then
      case "$cmd:$npos" in
%(position_values)s
      esac
    fi
  fi
  case "$kind" in
    name) candidates=(${(f)"$(multi-grok list --names 2>/dev/null)"}) ;;
    dir) _files -/; return ;;
    file) _files; return ;;
    shell) candidates=(%(shells)s) ;;
    proxy) candidates=(off inherit) ;;
    clash) candidates=(auto off) ;;
    shared) candidates=(${(f)"$(%(shared_source)s)"}) ;;
    statusline) candidates=(install uninstall) ;;
  esac
  compadd -- $candidates
}
compdef _multi_grok multi-grok
""" % {"option_cases": option_cases, "commands": _words(commands), "shells": _words(list(SHELLS)),
       "positional_cases": _positional_cases("      "), "path_options": "|".join(PATH_OPTIONS),
       "name_options": "|".join(NAME_OPTIONS), "option_values": option_values,
       "position_values": position_values, "shared_source": SHARED_SOURCE}


def _fish(specs: Dict[str, CommandSpec], positional: PositionalValues, options: OptionValues) -> str:
    commands = sorted(name for name in specs if name)
    lines = [
        "# fish completion for multi-grok (generated by `multi-grok completion fish`)",
        "complete -c multi-grok -f",
        "function __multi_grok_positionals",
        "    # 输出当前子命令之后已输入的位置参数个数（跳过选项与它的值）；遇到 -- 输出 -1。",
        "    set -l tokens (commandline -opc)",
        "    set -l value_options $argv",
        "    set -l seen 0",
        "    set -l skip 0",
        "    set -l count 0",
        "    for token in $tokens[2..-1]",
        "        if test $seen -eq 0",
        "            if not string match -q -- '-*' $token",
        "                set seen 1",
        "            end",
        "            continue",
        "        end",
        "        if test $skip -eq 1",
        "            set skip 0",
        "            continue",
        "        end",
        "        if test \"$token\" = '--'",
        "            echo -1",
        "            return",
        "        end",
        "        if string match -q -- '-*' $token",
        "            if contains -- $token $value_options",
        "                set skip 1",
        "            end",
        "            continue",
        "        end",
        "        set count (math $count + 1)",
        "    end",
        "    echo $count",
        "end",
        "complete -c multi-grok -n __fish_use_subcommand -a {}".format(shlex.quote(_words(commands))),
        "complete -c multi-grok -n __fish_use_subcommand -l help -l version",
    ]
    for name, spec in sorted(specs.items()):
        if not name:
            continue
        condition = "__fish_seen_subcommand_from {}".format(name)
        for option in spec.options:
            flag = "-l {}".format(option[2:]) if option.startswith("--") else "-s {}".format(option[1:])
            requires = " -r" if option in spec.value_options else ""
            files = " -F" if option in PATH_OPTIONS else ""
            names = " -a '(multi-grok list --names 2>/dev/null)'" if option in NAME_OPTIONS else ""
            if (name, option) in options:
                # -x = 必须带值且不补文件；-a 给出 choices 里的词。
                requires = " -x -a {}".format(shlex.quote(_words(options[(name, option)])))
            elif option in SHARED_OPTIONS and option in spec.value_options:
                requires = " -x -a {}".format(shlex.quote("({})".format(SHARED_SOURCE)))
            lines.append("complete -c multi-grok -n {} {}{}{}{}".format(
                shlex.quote(condition), flag, requires, files, names))
        vopts = _words(spec.value_options)
        for index, kind in enumerate(POSITIONAL_KINDS.get(name, [])):
            at_position = shlex.quote("{}; and test (__multi_grok_positionals {}) -eq {}".format(
                condition, vopts, index))
            if kind == "name":
                source = "'(multi-grok list --names 2>/dev/null)'"
            elif kind == "dir":
                source = "'(__fish_complete_directories)'"
            elif kind == "file":
                source = "'(__fish_complete_path)'"
            else:
                source = shlex.quote(_words(list(FIXED_WORDS[kind])))
            lines.append("complete -c multi-grok -n {} -a {}".format(at_position, source))
        for (command, index), words in sorted(positional.items()):
            if command != name:
                continue
            at_position = shlex.quote("{}; and test (__multi_grok_positionals {}) -eq {}".format(
                condition, vopts, index))
            lines.append("complete -c multi-grok -n {} -a {}".format(at_position, shlex.quote(_words(words))))
    return "\n".join(lines) + "\n"
