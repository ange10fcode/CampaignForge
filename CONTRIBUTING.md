# Contributing

Contributions are welcome. Keep changes focused on world consistency, hierarchy rules, generation quality, persistence, or usability.

1. Fork the repository.
2. Create a feature branch.
3. Run `python -m py_compile campaign_forge.py campaignforge/*.py`.
4. Test world, town, road, ocean, building, and room generation.
5. Open a pull request with screenshots when the change is visual.

Biome restrictions and hierarchy rules should be enforced by shared generation logic rather than one-off UI checks.
