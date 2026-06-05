#!/usr/bin/env python3
"""
Jarvis CLI — interactive terminal interface for Jarvis personal AI.
Usage:
  jarvis                    Interactive chat
  jarvis "question"         Single query
  jarvis --stats            Memory and model stats
  jarvis --index            Trigger indexing
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests
import ollama
import yaml

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
sys.path.insert(0, str(JARVIS_HOME))
CONFIG_FILE = JARVIS_HOME / "config" / "jarvis.yaml"

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
    from rich import print as rprint
    RICH = True
except ImportError:
    RICH = False

try:
    from executor import detect_action, run_action, get_action_info, AUTO, CONFIRM, APPROVE
    EXECUTOR_AVAILABLE = True
except ImportError:
    EXECUTOR_AVAILABLE = False

console = Console() if RICH else None


def load_config():
    if not CONFIG_FILE.exists():
        return {
            "model": {"primary": "deepseek-r1:7b", "fallback": "qwen2.5-coder:7b"},
            "memory": {"path": str(JARVIS_HOME / "memory"), "embedding_model": "nomic-embed-text"},
            "owner": "Mike",
        }
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


CONFIG = load_config()
PRIMARY_MODEL = CONFIG["model"]["primary"]
FALLBACK_MODEL = CONFIG["model"].get("fallback", "qwen2.5-coder:7b")
EMBED_MODEL = CONFIG["memory"].get("embedding_model", "nomic-embed-text")
MEMORY_PATH = CONFIG["memory"]["path"]
OWNER = CONFIG.get("owner", "Mike")
OPENCLAW_ENABLED = CONFIG.get("openclaw", {}).get("enabled", False)
API_PORT = CONFIG.get("interfaces", {}).get("api_port", 8181)
API_BASE = os.environ.get("JARVIS_API_URL", f"http://127.0.0.1:{API_PORT}")


def get_collection():
    try:
        import chromadb
        Path(MEMORY_PATH).mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=MEMORY_PATH)
        return client.get_or_create_collection(
            name="jarvis_memory",
            metadata={"hnsw:space": "cosine"},
        )
    except Exception:
        return None


def get_embedding(text):
    try:
        response = ollama.embeddings(model=EMBED_MODEL, prompt=text[:4096])
        return response["embedding"]
    except Exception:
        return None


def retrieve_context(query, n=5):
    col = get_collection()
    if col is None or col.count() == 0:
        return []
    emb = get_embedding(query)
    if emb is None:
        return []
    try:
        results = col.query(
            query_embeddings=[emb],
            n_results=min(n, col.count()),
            include=["documents", "metadatas", "distances"],
        )
        return [
            {
                "text": results["documents"][0][i],
                "source_type": results["metadatas"][0][i].get("source_type", ""),
                "distance": results["distances"][0][i],
            }
            for i in range(len(results["ids"][0]))
            if results["distances"][0][i] < 0.8
        ]
    except Exception:
        return []


def build_system_prompt(context_chunks, action_result=None):
    ctx_block = ""
    if context_chunks:
        parts = [f"[{c['source_type']}] {c['text'][:400]}" for c in context_chunks]
        ctx_block = "\n\nRelevant context from your work:\n" + "\n\n".join(parts)

    action_block = ""
    if action_result:
        status = "succeeded" if action_result.get("success") else "failed"
        action_block = (
            f"\n\nAction '{action_result['action']}' just ran and {status}. Output:\n"
            f"```\n{action_result['output'][:2500]}\n```\n"
            "Summarize and interpret this output for Mike. Be concise."
        )

    executor_note = ""
    if EXECUTOR_AVAILABLE and OPENCLAW_ENABLED:
        executor_note = (
            "\n\nYou have OpenClaw: you can run real commands on this Kali machine. "
            "When asked to check system status, run apps, or manage services, "
            "acknowledge that the action is being executed, not just describe how to do it."
        )

    return (
        f"You are Jarvis, {OWNER}'s personal AI assistant running on their Kali workstation. "
        f"You have memory of their projects, code, shell commands, and AI sessions. "
        f"Be concise, direct, and technically precise. "
        f"When answering about their work, cite the source type if relevant."
        f"{executor_note}{ctx_block}{action_block}"
    )


def ask_jarvis(query, history=None, model=None, action_result=None):
    model = model or PRIMARY_MODEL
    context = retrieve_context(query)
    system = build_system_prompt(context, action_result=action_result)

    messages = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": query})

    try:
        stream = ollama.chat(
            model=model,
            messages=messages,
            stream=True,
            options={"temperature": 0.7, "num_predict": 2048},
        )
        return stream, context, model
    except ollama.ResponseError:
        if model != FALLBACK_MODEL:
            try:
                stream = ollama.chat(
                    model=FALLBACK_MODEL,
                    messages=messages,
                    stream=True,
                    options={"temperature": 0.7, "num_predict": 2048},
                )
                return stream, context, FALLBACK_MODEL
            except Exception as e:
                raise RuntimeError(f"Both models failed: {e}")
        raise


def print_response_stream(stream, model):
    full_response = ""
    if RICH:
        console.print(f"\n[bold cyan]Jarvis[/bold cyan] [dim]({model})[/dim]: ", end="")
    else:
        print(f"\nJarvis ({model}): ", end="", flush=True)

    for chunk in stream:
        token = chunk["message"]["content"]
        full_response += token
        if RICH:
            console.print(token, end="")
        else:
            print(token, end="", flush=True)

    print()
    return full_response


def print_full_response(text, model):
    if RICH:
        console.print(f"\n[bold cyan]Jarvis[/bold cyan] [dim]({model})[/dim]: {text}")
    else:
        print(f"\nJarvis ({model}): {text}")
    return text


def ask_api(query, history=None):
    """Route query through /query (smart model routing, Jarvis persona, strip_thinking)."""
    payload = {"query": query}
    if history:
        payload["history"] = [{"role": m["role"], "content": m["content"]} for m in history]
    resp = requests.post(f"{API_BASE}/query", json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    return data["response"], data["model"], data.get("context_used", 0)


def run_shell_command(cmd: str) -> str:
    """Run an arbitrary shell command and return output."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60,
            env={**os.environ, "HOME": str(Path.home())}
        )
        output = (result.stdout + result.stderr).strip()
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out."
    except Exception as e:
        return f"Error: {e}"


def handle_action(user_input: str, history: list) -> tuple[bool, str | None]:
    """
    Detect and execute an OpenClaw action.
    Returns (handled, response_text).
    handled=True means the input was an action (whether or not it ran).
    """
    if not EXECUTOR_AVAILABLE or not OPENCLAW_ENABLED:
        return False, None

    action_name = detect_action(user_input)
    if not action_name:
        return False, None

    info = get_action_info(action_name)
    tier = info["tier"]
    desc = info["desc"]

    # Tier gate
    if tier == CONFIRM:
        if RICH:
            confirm = Prompt.ask(
                f"\n[yellow]⚡ Action:[/yellow] {desc}  [dim](confirm tier)[/dim]\n  Run it? [y/N]"
            ).strip().lower()
        else:
            confirm = input(f"\n⚡ Action: {desc} (confirm required) — Run it? [y/N] ").strip().lower()
        if confirm != "y":
            if RICH:
                console.print("[dim]Cancelled.[/dim]")
            else:
                print("Cancelled.")
            return True, None

    elif tier == APPROVE:
        if RICH:
            confirm = Prompt.ask(
                f"\n[red bold]⚠ HIGH-RISK ACTION:[/red bold] {desc}\n  Type [bold]yes[/bold] to approve"
            ).strip().lower()
        else:
            confirm = input(f"\n⚠ HIGH-RISK: {desc} — Type 'yes' to approve: ").strip().lower()
        if confirm != "yes":
            if RICH:
                console.print("[dim]Cancelled.[/dim]")
            else:
                print("Cancelled.")
            return True, None

    # Run
    if RICH:
        console.print(f"[dim]  → Running: {desc}...[/dim]")
    else:
        print(f"  → Running: {desc}...")

    result = run_action(action_name)

    # Show raw output
    if RICH:
        console.print(f"\n[dim]──── raw output ────[/dim]")
        console.print(result["output"])
        console.print(f"[dim]────────────────────[/dim]")
    else:
        print(f"\n---- output ----\n{result['output']}\n----------------")

    return True, result


def show_stats():
    col = get_collection()
    if RICH:
        table = Table(title="Jarvis System Stats", show_header=True)
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green")

        if col:
            count = col.count()
            table.add_row("Memory chunks", str(count))
            if count > 0:
                results = col.get(limit=min(100, count), include=["metadatas"])
                type_counts = {}
                for m in results["metadatas"]:
                    t = m.get("source_type", "unknown")
                    type_counts[t] = type_counts.get(t, 0) + 1
                for t, c in sorted(type_counts.items()):
                    table.add_row(f"  {t}", str(c))
        else:
            table.add_row("Memory", "unavailable")

        try:
            models = ollama.list()
            model_names = [m.model if hasattr(m, "model") else m.get("name", "") for m in models.models]
            table.add_row("Ollama models", ", ".join(model_names) or "none")
        except Exception:
            table.add_row("Ollama", "not running")

        table.add_row("Primary model", PRIMARY_MODEL)
        table.add_row("Embed model", EMBED_MODEL)
        table.add_row("Jarvis home", str(JARVIS_HOME))
        table.add_row("OpenClaw", "enabled" if (EXECUTOR_AVAILABLE and OPENCLAW_ENABLED) else "disabled")
        console.print(table)
    else:
        print("\n=== Jarvis Stats ===")
        if col:
            print(f"Memory chunks: {col.count()}")
        try:
            models = ollama.list()
            print(f"Models: {[m.model if hasattr(m, 'model') else m.get('name', '') for m in models.models]}")
        except Exception:
            print("Ollama: not running")
        print(f"Primary model: {PRIMARY_MODEL}")
        print(f"OpenClaw: {'enabled' if (EXECUTOR_AVAILABLE and OPENCLAW_ENABLED) else 'disabled'}")


def interactive_mode():
    openclaw_status = "OpenClaw ON" if (EXECUTOR_AVAILABLE and OPENCLAW_ENABLED) else "no actions"
    if RICH:
        console.print(Panel(
            f"[bold cyan]Jarvis[/bold cyan] — {OWNER}'s Personal AI\n"
            f"[dim]Model: {PRIMARY_MODEL} | {openclaw_status} | '!' prefix runs shell | 'exit' to quit[/dim]",
            border_style="cyan",
        ))
    else:
        print(f"\n=== Jarvis — {OWNER}'s Personal AI ===")
        print(f"Model: {PRIMARY_MODEL} | {openclaw_status} | Type 'exit' to quit\n")

    history = []
    while True:
        try:
            if RICH:
                user_input = Prompt.ask(f"\n[bold green]{OWNER}[/bold green]").strip()
            else:
                user_input = input(f"\n{OWNER}: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "bye"):
            print("Goodbye.")
            break
        if user_input.lower() in ("--stats", "/stats"):
            show_stats()
            continue
        if user_input.lower() in ("--index", "/index"):
            trigger_index()
            continue

        # !cmd — run raw shell command and feed output into the conversation
        if user_input.startswith("!"):
            cmd = user_input[1:].strip()
            if not cmd:
                continue
            if RICH:
                console.print(f"[dim]  $ {cmd}[/dim]")
            output = run_shell_command(cmd)
            if RICH:
                console.print(output)
            else:
                print(output)
            # Inject into history so follow-up questions have context
            history.append({"role": "user", "content": f"I ran: {cmd}"})
            history.append({"role": "assistant", "content": f"Output:\n```\n{output[:2000]}\n```"})
            if len(history) > 20:
                history = history[-20:]
            continue

        # OpenClaw action detection
        handled, action_result = handle_action(user_input, history)
        if handled:
            if action_result is not None:
                # Send output to LLM for interpretation
                try:
                    stream, context, used_model = ask_jarvis(
                        user_input, history, action_result=action_result
                    )
                    response = print_response_stream(stream, used_model)
                    if context and RICH:
                        console.print(f"[dim]  (context: {len(context)} chunks)[/dim]")
                    history.append({"role": "user", "content": user_input})
                    history.append({"role": "assistant", "content": response})
                    if len(history) > 20:
                        history = history[-20:]
                except Exception as e:
                    if RICH:
                        console.print(f"[red]LLM error: {e}[/red]")
                    else:
                        print(f"LLM error: {e}")
            continue

        # Normal chat — route through API for smart routing, memory, and action planning
        response = None
        try:
            response, used_model, ctx_count = ask_api(user_input, history)
            print_full_response(response, used_model)
            if ctx_count and RICH:
                console.print(f"[dim]  (context: {ctx_count} chunks from memory)[/dim]")
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
            if RICH:
                console.print("[dim]  (API unavailable — using Ollama direct)[/dim]")
            else:
                print("  (API unavailable — using Ollama direct)")
            try:
                stream, context, used_model = ask_jarvis(user_input, history)
                response = print_response_stream(stream, used_model)
                if context and RICH:
                    console.print(f"[dim]  (context: {len(context)} chunks)[/dim]")
            except Exception as e:
                if RICH:
                    console.print(f"[red]Error: {e}[/red]")
                else:
                    print(f"Error: {e}")
        except Exception as e:
            if RICH:
                console.print(f"[red]Error: {e}[/red]")
            else:
                print(f"Error: {e}")

        if response:
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": response})
            if len(history) > 20:
                history = history[-20:]


def trigger_index():
    print("Triggering indexing...")
    try:
        from indexer import load_config, run_indexing
        cfg = load_config()
        collection = run_indexing(cfg)
        print(f"Done. Total chunks: {collection.count()}")
    except Exception as e:
        print(f"Indexing error: {e}")


def single_query(query):
    # Check for action first (CONFIRM/APPROVE tier — needs interactive terminal)
    handled, action_result = handle_action(query, [])
    if handled and action_result is not None:
        try:
            stream, context, used_model = ask_jarvis(query, action_result=action_result)
            print_response_stream(stream, used_model)
        except Exception as e:
            print(f"Error: {e}")
        return
    if handled:
        return

    # Route through API for smart routing, memory, and action planning
    try:
        response, used_model, ctx_count = ask_api(query)
        print_full_response(response, used_model)
        if ctx_count:
            if RICH:
                console.print(f"[dim]  (context: {ctx_count} chunks from memory)[/dim]")
            else:
                print(f"\n[context: {ctx_count} memory chunks used]")
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        try:
            stream, context, used_model = ask_jarvis(query)
            response = print_response_stream(stream, used_model)
            if context:
                print(f"\n[context: {len(context)} memory chunks used]")
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis — Personal AI Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="?", help="Ask Jarvis a question")
    parser.add_argument("--stats", action="store_true", help="Show memory and model stats")
    parser.add_argument("--index", action="store_true", help="Trigger memory indexing")
    parser.add_argument("--model", type=str, help=f"Override model (default: {PRIMARY_MODEL})")
    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.index:
        trigger_index()
    elif args.query:
        single_query(args.query)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
