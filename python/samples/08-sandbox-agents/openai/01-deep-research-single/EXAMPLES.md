# Deep Research Agent - Ready-to-Run Examples

This file contains copy-paste commands for interesting research tasks.

## Example 1: Analyze OpenAI Agents SDK
```bash
python deep_research_agent.py \
  --repo https://github.com/openai/openai-agents-python \
  "What are the main agent capabilities and how do they work together?"
```

## Example 2: Understand Kubernetes Scheduler
```bash
python deep_research_agent.py \
  --repo https://github.com/kubernetes/kubernetes \
  "How does the Kubernetes scheduler decide which node to place a pod on?"
```

## Example 3: Explore FastAPI Performance
```bash
python deep_research_agent.py \
  --repo https://github.com/tiangolo/fastapi \
  "What makes FastAPI fast? Explain the async architecture and performance optimizations."
```

## Example 4: Research LangChain Architecture
```bash
python deep_research_agent.py \
  --repo https://github.com/langchain-ai/langchain \
  "What are the core abstractions in LangChain? How do chains, agents, and tools fit together?"
```

## Example 5: Analyze PyTorch Design
```bash
python deep_research_agent.py \
  --repo https://github.com/pytorch/pytorch \
  "How does PyTorch implement autograd and backpropagation?"
```

## Example 6: Explore Docker Architecture
```bash
python deep_research_agent.py \
  --repo https://github.com/moby/moby \
  "Explain how Docker implements container isolation using namespaces and cgroups."
```

## Example 7: Study React Rendering
```bash
python deep_research_agent.py \
  --repo https://github.com/facebook/react \
  "How does React's virtual DOM and reconciliation algorithm work?"
```

## Example 8: Analyze This Repo (ACA Sandboxes)
```bash
python deep_research_agent.py \
  "How does ACA Sandboxes implement egress control and default-deny networking?"
```

## Custom Questions

Replace the question with your own:
```bash
python deep_research_agent.py \
  --repo https://github.com/user/repo \
  "Your question here"
```
