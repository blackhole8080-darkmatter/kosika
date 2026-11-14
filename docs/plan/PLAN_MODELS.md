# Plan: Kosika's own models and a cloud-independent virtual-model router

Goal: make kosika-coder and kosika-small, Kosika-written models trained with std/learn.kos and std/layers.kos, and the virtual-model router in kosika/virtual_model.py, smart and independent enough of cloud models that Kosika coder agents run entirely on local models. A model declaration describes a typed call but creates no provider, so this plan builds the declarations, the training, the local inference, the router, the agent bindings, the self-learning loop, and the offline proof, in that order.

## Step 1: Declare kosika-small and kosika-coder as Kosika models

Why it matters: nothing can route to or train a model until the program declares it with typed input and output records. Declaring the two Kosika-owned models gives agents and workflows stable names to address, keeps every call schema-checked, and makes the models first-class local targets instead of cloud endpoints.

Files touched: a new Kosika source file holding the two model declarations (for example kosika/models.kos or an examples file), plus kosika/model.py for the provider-neutral model types that declarations plug into.

Done when: kosika check accepts both declarations, their input and output records are closed and typed, and a workflow can name kosika-small or kosika-coder in a use_model call.

## Step 2: Write std/layers.kos, neural network layers in Kosika

Why it matters: models must be ordinary Kosika records so they train on the laptop processor, fit in memory, and appear in traces like any other value, as std/nn.kos already demonstrates. layers.kos provides the layer primitives—dense layer, activation, forward pass—so nets are built from Kosika code, not from an external framework.

Files touched: new kosika/std/layers.kos and its native mirror native/std/layers.kos; std/nn.kos stays available as the existing net built-in. No change to the language contract.

Done when: both the Python engine and the native engine check a program that constructs a layer and runs a forward pass, and the resulting net remains a plain record storable in memory.

## Step 3: Write std/learn.kos, training in Kosika

Why it matters: gradient descent, loss, and the training loop must run on the processor with no GPU and no external library, so a Kosika program can train its own models from examples it has collected. learn.kos turns layers.kos into something that improves.

Files touched: new kosika/std/learn.kos and its native mirror native/std/learn.kos.

Done when: a small network trains on toy examples in both engines, its measured loss decreases, and the checker accepts the training program.

## Step 4: Run Kosika-trained nets locally with no model server

Why it matters: a trained net must answer prompts directly inside the Python runtime so kosika-coder and kosika-small work with zero network and no provider failure when offline. Local inference is what makes the models truly independent of cloud models.

Files touched: kosika/model.py to add a local Kosika model provider that answers from a Net record; kosika/runtime.py if the runtime needs to expose local model execution to workflows.

Done when: kosika run with a local model binding returns answers computed by predict from a trained Kosika net, with no HTTP request and no cloud provider involved.

## Step 5: Teach the virtual-model router to choose local Kosika models

Why it matters: kosika/virtual_model.py today wraps a host-authorized model and retrieves local passages as reference material. It must be able to select kosika-small or kosika-coder as the underlying model instead of a cloud endpoint, and route a prompt to the right one by task type.

Files touched: kosika/virtual_model.py and kosika/model.py for the local model bindings the router uses.

Done when: a coding prompt sent to the router is answered by kosika-coder locally, with source citations, and no cloud model request is made.

## Step 6: Bind Kosika coder agents to the local models

Why it matters: agents run ask steps against a model, and the coder agent must name kosika-coder so code-writing work happens locally. Without this binding, agents still reach for whichever cloud model the host supplies.

Files touched: kosika/agent.py, kosika/agent_steps.py, and kosika/model_steps.py, where agent model declarations and model-call steps are resolved to providers.

Done when: an agent declared with model: kosika-coder writes a Kosika function in an offline run, and its trace shows only local model calls.

## Step 7: Learn from accepted answers and retrain the local models

Why it matters: the models should get smarter from their own work. kosika/learning.py and kosika/lessons.py capture accepted answers as experiences, and std/learn.kos retrains kosika-small and kosika-coder from them, so repeated tasks are answered from lessons instead of from scratch.

Files touched: kosika/learning.py, kosika/lessons.py, kosika/std/learn.kos, and the memory folder where lesson files are kept.

Done when: a task repeated after a first accepted answer is answered from a retained lesson file in the memory folder, and retraining on kept examples lowers measured loss.

## Step 8: Evaluate the local models offline with kosika measure

Why it matters: kosika measure FILE --model NAME runs each eval on each named model and reports how many runs passed. Evals must exercise the real local models, not scripted cloud answers, so they prove kosika-coder and kosika-small are good enough to run agents.

Files touched: kosika/cli.py and kosika/evaluation.py for the measure path, plus a local eval suite file.

Done when: kosika measure on the local eval suite passes for both kosika-small and kosika-coder without any internet access.

## Step 9: Gate the router on local confidence, with no silent cloud fallback

Why it matters: independence means the router must say it does not know or refuse rather than fall back to a cloud model when the local models are unsure. Confidence gating keeps the system honest about what it can actually do locally.

Files touched: kosika/virtual_model.py for confidence scoring and refusal behavior.

Done when: an out-of-domain prompt receives a local refusal instead of any cloud provider request, and the router only answers locally when its confidence threshold is met.

## Step 10: Prove coder agents run end to end offline

Why it matters: the whole plan only matters if the coder agents run completely on Kosika's own models with all cloud bindings removed, and traces still validate by replay without a model server.

Files touched: a new test file in tests/ for the offline coder scenario, and kosika/trace_replay.py if replay needs to confirm no model call is required.

Done when: the offline test suite passes, and replay of a coder-agent trace validates successfully with no model server reachable.
