"""Decibyl-specific subclasses of pipecat realtime LLM services.

Each subclass wires Decibyl engine integration quirks (user-mute gating,
TTSSpeakFrame greeting trigger, node-transition handling, function-call
deferral, etc.) onto the corresponding pipecat realtime service.

The pipecat fork's services stay close to upstream — Decibyl behavior lives
here.
"""
