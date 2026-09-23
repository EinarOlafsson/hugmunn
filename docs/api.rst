Python API reference
====================

Import these names from ``hugmunn``. See :doc:`python-api` for complete examples.
The reference below is built from the implementation's docstrings.

Agents and events
-----------------

.. autoclass:: hugmunn.Agent
   :members:

.. autofunction:: hugmunn.agent

.. autoclass:: hugmunn.Event
   :members:

Model discovery
---------------

.. autoclass:: hugmunn.Model
   :members:

.. autofunction:: hugmunn.models
.. autofunction:: hugmunn.available_models
.. autofunction:: hugmunn.skills
.. autofunction:: hugmunn.tool_names

Credentials
-----------

.. autofunction:: hugmunn.sign_in
.. autofunction:: hugmunn.signed_in

Settings
--------

.. autoclass:: hugmunn.Effort
   :members:
   :undoc-members:

.. autoclass:: hugmunn.Persistence
   :members:
   :undoc-members:

.. autoclass:: hugmunn.Autonomy
   :members:
   :undoc-members:

Saved conversations
-------------------

.. autoclass:: hugmunn.Session
   :members:

.. autofunction:: hugmunn.sessions

Exceptions
----------

.. autoexception:: hugmunn.HugmunnError
.. autoexception:: hugmunn.ModelNotFound
.. autoexception:: hugmunn.ApprovalRequired
.. autoexception:: hugmunn.ServerError
.. autoexception:: hugmunn.ProviderError

Version
-------

.. autodata:: hugmunn.__version__
