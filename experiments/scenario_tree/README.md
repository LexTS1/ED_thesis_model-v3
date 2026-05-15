# Scenario Tree Artifacts

This directory is the default local workspace for generated scenario-tree files.

The model writes generated leaf configs, run folders, logs, manifests, and
summary tables here. Those artifacts are intentionally ignored by git because
they are reproducible from `config/scenario_tree/` and can become large or
stale quickly.

Regenerate the experiment space with:

```bash
PYTHONPATH=src python3 -m model_v3.scenario_tree.create_scenario_tree_space --config-root config/scenario_tree --experiment-root experiments/scenario_tree --print-summary
```
