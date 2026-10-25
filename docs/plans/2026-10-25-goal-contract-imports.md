# Goal contracts and configuration imports

Full CI found that importing Settings loaded observation business logic through
Life's Goal proposal types. The existing data classes now live in
`goal_protocol.py`, while `goals.py` and `goal_plan.py` use those same classes for
execution. Life configuration imports the data module directly. Field validation,
serialized values and existing Goal execution responsibilities remain identical.

This follows the repository's storage-free `media_config.py` and
`social_autonomy_protocol.py` boundaries. The inspected DSH schedule
[runtime](https://github.com/deepseek-ai/deepseek-harness/blob/22ddee3792402c3e70ec5f86a63c3e3e895a2f60/packages/schedule/schedule/src/runtime.ts)
also imports its record types separately from execution and storage.
[Python modules](https://docs.python.org/3/tutorial/modules.html) provide one shared
definition per imported module; the same
[Pydantic models](https://pydantic.dev/docs/validation/latest/concepts/models/)
retain their validators and serialization. There is no new wire format or storage
representation. A subprocess regression forbids Goal and observation runtime
imports while parsing and round-tripping Settings.

Voice's isolated contract import fixture includes the existing dependency-free
authority wire package. It continues to reject Core, storage, Bridge and Voice
runtime imports rather than treating the shared wire types as an execution host.
