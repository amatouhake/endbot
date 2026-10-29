# endbot-core

Shared, Endstone-free logic for Endbot: principals, the operation registry, the command grammar, authorization and
controller policy, Groups, the script store and i18n.

Both the Endstone plugin and `endbotd` import this package, so it must not import `endstone`. Tests live with the
plugin suite today because they exercise the same behaviour through the command surface:

```bash
PYTHONPATH=plugin/endbot/src:core/src python3 -m unittest discover -s plugin/endbot/tests -v
```
