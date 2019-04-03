# Copyright 2017 The Sonnet Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or  implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

"""Utility functions for dealing with Sonnet Modules.
https://github.com/deepmind/sonnet/blob/master/sonnet/python/modules/util.py"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import re
import six
import math
import abc
import tensorflow as tf
import tensorflow_hub as tf_hub
from tensorflow.python.ops import array_ops
from tensorflow.python.framework import dtypes
from tensorflow.python.ops import rnn as rnn_ops
from tensorflow.python.ops import rnn_cell_impl
from warnings import warn
from constants import CACHED_ELMO_NUM_ELEMENTS


@six.add_metaclass(abc.ABCMeta)
class AbstractModule(object):
    """Abstract encoder class.
       https://github.com/deepmind/sonnet/blob/master/sonnet/python/modules/base.py
    """

    def __init__(self, _sentinel=None, name=None):
        """Performs the initialisation necessary for all AbstractModule instances.

        Every subclass of AbstractModule must begin their constructor with a call to
        this constructor, i.e.

        `super(MySubModule, self).__init__(custom_getter=custom_getter, name=name)`.

        If you instantiate sub-modules in __init__ you must create them within the
        `_enter_variable_scope` context manager to ensure they are in the module's
        variable scope. Alternatively, instantiate sub-modules in `_build`.

        Args:
          _sentinel: Variable that only carries a non-None value if `__init__` was
              called without named parameters. If this is the case, a deprecation
              warning is issued in form of a `ValueError`.
          custom_getter: Callable or dictionary of callables to use as
            custom getters inside the module. If a dictionary, the keys
            correspond to regexes to match variable names. See the `tf.get_variable`
            documentation for information about the custom_getter API.
          name: Name of this module. Used to construct the Templated build function.
              If `None` the module's class name is used (converted to snake case).

        Raises:
          TypeError: If `name` is not a string.
          TypeError: If a given `custom_getter` is not callable.
          ValueError: If `__init__` was called without named arguments.
        """

        if _sentinel is not None:
            raise ValueError("Calling AbstractModule.__init__ "
                             "without named arguments is not supported.")

        if name is None:
            name = self.__class__.__name__.upper()
        elif not isinstance(name, six.string_types):
            raise TypeError("Name must be a string.")

        self._template = tf.make_template(
            name_=name,
            func_=self._build_wrapper,
            create_scope_now_=True)

        self._original_name = name
        self._unique_name = self._template.variable_scope.name.split("/")[-1]

        # Update __call__ and the object docsto enable better introspection.
        self.__doc__ = self._build.__doc__
        self.__call__.__func__.__doc__ = self._build.__doc__

        # Container for all variables created in this
        # module and its sub-modules.
        self._all_variables = set([])

    def _build_wrapper(self, *args, **kwargs):
        """Function which will be wrapped in a Template to do variable sharing.
        Passes through all arguments to the _build method, and returns the
        corresponding outputs, plus the name_scope generated by this call of
        the template.
        Args:
          *args: args list for self._build
          **kwargs: kwargs dict for self._build
        Returns:
          A tuple containing (output from _build, scope_name).
        """
        output = self._build(*args, **kwargs)
        # Make a dummy subscope to check the name scope we are in.
        # We could read the name scope from one of the outputs produced,
        # except that the outputs could have been produced from a subscope
        # instantiated by the build function, for example if inner modules
        # are present. Calling name_scope here and creating a new subscope
        # guarantees we get the right answer. Because we don't create an ops
        # inside this dummy scope, no extra memory will be consumed.
        with tf.name_scope("dummy") as scope_name:
            this_scope_name = scope_name[:-len("/dummy/")]
        return output, this_scope_name

    def _check_init_called(self):
        """Checks that the base class's __init__ method has been called.
        Raises:
          NotImplementedError: `AbstractModule.__init__` has not been called.
        """
        try:
            self._template
        except AttributeError:
            raise NotImplementedError(
                "You may have forgotten to call super at the "
                "start of %s.__init__." % self.__class__.__name__)

    @abc.abstractmethod
    def _build(self, *args, **kwargs):
        """Add elements to the Graph, computing output Tensors from input Tensors.
        Subclasses must implement this , which will be wrapped in Template.
        Args:
          *args: Input Tensors.
          **kwargs: Additional Python flags controlling connection.
        Returns:
          output Tensor(s).
        """

    def __call__(self, *args, **kwargs):
        """Operator overload for calling.
        This is the entry point when users connect a Module into the Graph. The
        underlying _build method will have been wrapped in a Template by the
        constructor, and we call this template with the provided inputs here.
        Args:
          *args: Arguments for underlying _build method.
          **kwargs: Keyword arguments for underlying _build method.
        Returns:
          The result of the underlying _build method.
        """
        self._check_init_called()
        outputs, subgraph_name_scope = self._template(*args, **kwargs)
        return outputs

    @property
    def variable_scope(self):
        """Returns the variable_scope declared by the module."""
        return self._template.variable_scope

    @property
    def scope_name(self):
        """Returns the full name of the Module's variable scope."""
        return self._template.variable_scope.name

    @property
    def module_name(self):
        """Returns the name of the Module."""
        return self._unique_name

    def get_variables(self, collection=tf.GraphKeys.TRAINABLE_VARIABLES):
        """Returns tuple of `tf.Variable`s declared inside this module.

        Note that this operates by searching this module's variable scope,
        and so does not know about any modules that were constructed elsewhere but
        used inside this module.

        This method explicitly re-enters the Graph which this module has been
        connected to.

        Args:
          collection: Collection to restrict query to. By default this is
            `tf.Graphkeys.TRAINABLE_VARIABLES`, which doesn't include non-trainable
            variables such as moving averages.

        Returns:
          A tuple of `tf.Variable` objects.

        Raises:
          NotConnectedError: If the module is not connected to the Graph.
        """
        # Explicitly re-enter Graph, in case the module is being queried with a
        # different default Graph from the one it was connected to. If this was not
        # here then querying the variables from a different graph scope would
        # produce an empty tuple.
        
        # with self._graph.as_default():
        return utils.get_variables_in_scope(
            self.variable_scope, collection=collection)

    def clone(self, name=None):
        """Returns a cloned module.
        
        Args:
            name: Optional string assigning name of cloned module.
            The default name is constructed by appending "_clone"
            to `self.module_name`.
        
        Returns:
            Cloned module.
        """
        if name is None:
            name = self.module_name + "_clone"

        return self._clone(name=name)

    @abc.abstractmethod
    def _clone(self, name):
        """Clone module"""



def _embedding_dim(vocab_size):
    """Calculate a reasonable embedding size for a vocabulary.
    Rule of thumb is 6 * 4th root of vocab_size.
    Args:
      vocab_size: Size of the input vocabulary.
    Returns:
      The embedding size to use.
    Raises:
      ValueError: if `vocab_size` is invalid.
    """
    if not vocab_size or (vocab_size <= 0):
        raise ValueError("Invalid vocab_size %g." % vocab_size)
    return int(round(6.0 * math.sqrt(math.sqrt(vocab_size))))


class Embeddding(base.AbstractModule):
    """Module for embedding tokens in a low-dimensional space.

        TOPO:
        Add Initializer to the embedding
    """

    def __init__(self,
                 vocab_size=None,
                 embed_dim=None,
                 existing_vocab=None,
                 trainable=True,
                 name="embed"):

        if vocab_size is None and existing_vocab is None:
            raise ValueError("both `vocab_size` and `existing_vocab` are none")

        if existing_vocab is not None and not all(
                x is None for x in [vocab_size, embed_dim]):
            raise ValueError("When `existing_vocab` is provided, some of the "
                             "arguments should not be provided.")

        super(Embeddding, self).__init__(name=name)
        if existing_vocab is None:
            embed_dim = embed_dim or _embedding_dim(self._vocab_size)
        else:
            existing_vocab = tf.convert_to_tensor(
                existing_vocab, dtype=tf.float32)
            existing_vocab_shape = existing_vocab.get_shape().with_rank(2)
            existing_vocab_shape.assert_is_fully_defined()
            vocab_size, embed_dim = existing_vocab_shape.as_list()

        self._vocab_size = vocab_size
        self._embed_dim = embed_dim
        self._existing_vocab = existing_vocab
        self._trainable = trainable
        self._initializer = utils.create_linear_initializer(vocab_size)

    def _build(self, ids):
        """Lookup embeddings."""
        if self._existing_vocab is None:
            self._embeddings = tf.get_variable(
                "embeddings",
                shape=[self._vocab_size, self._embed_dim],
                dtype=tf.float32,
                initializer=self._initializer,
                trainable=self._trainable)
        else:
            self._embeddings = tf.get_variable(
                "embeddings",
                dtype=tf.float32,
                initializer=self._existing_vocab,
                trainable=self._trainable)

        # Lookup embeddings
        return tf.nn.embedding_lookup(
            self._embeddings, ids, name="embedding_lookup")

    @property
    def vocab_size(self):
        """Size of input vocabulary."""
        return self._vocab_size

    @property
    def embed_dim(self):
        """Size of embedding vectors."""
        return self._embed_dim

    @property
    def embeddings(self):
        """Returns the Variable containing embeddings."""
        return self._embeddings

    def _clone(self, name):
        return type(self)(vocab_size=self._vocab_size,
                          embed_dim=self._embed_dim,
                          existing_vocab=self._existing_vocab,
                          trainable=self._trainable,
                          name=name)


class TFHubElmoEmbedding(base.AbstractModule):
    """Module for embdding tokens using TF-Hub ELMO
        
       More information regarding the ELMO Module can be found in
       https://alpha.tfhub.dev/google/elmo/2
    """
    
    ELMO_URL = "https://tfhub.dev/google/elmo/2"

    def __init__(self, trainable=False, name="elmo_embed"):
        super(TFHubElmoEmbedding, self).__init__(name=name)
        self._trainable = trainable
        self._elmo = tf_hub.Module(self.ELMO_URL, trainable=trainable)

    def _build(self, tokens_input, tokens_length):
        """Compute the ELMO embeddings

        Args:
            tokens_input: tf.string Tensor of [batch_size, max_length]
            tokens_length: tf.int32 Tensor of [batch_size]

        Returns:
            embeddings: weighted sum of 3 layers (from ELMO model)
                        [batch_size, max_length, 1024]
        """
        if tokens_input.dtype != tf.string:
            raise TypeError("`tokens_input` must be tf.string")

        embeddings = self._elmo(
            inputs={"tokens": tokens_input,
                    "sequence_len": tokens_length},
            signature="tokens",
            as_dict=True)["elmo"]

        return embeddings

    def _clone(self, name):
        return type(self)(trainable=self._trainable, name=name)


class LstmEncoder(base.AbstractModule):
    """LSTM Encoder."""

    def __init__(self,
                 unit_type,
                 num_units,
                 num_layers=1,
                 dropout_rate=None,
                 num_residual_layers=0,
                 scope="LstmEncoder",
                 is_training=True,  # only for dropout
                 bidirectional=True,
                 name="LstmEncoder",
                 **encoder_kargs):

        super(LstmEncoder, self).__init__(name=name)

        self._encoder_scope = scope
        self._is_training = is_training
        self._bidirectional = bidirectional

        self._unit_type = unit_type
        self._num_units = num_units
        self._num_layers = num_layers
        self._dropout_rate = dropout_rate
        self._num_residual_layers = num_residual_layers

        self._encoder_kargs = encoder_kargs
        
        if encoder_kargs:
            print("Additional RNN Cell Arguments: \n")
            addi_info = ["\t\t\t %s \t %s " % (k, v)
                         for k, v in encoder_kargs.items()]
            print("\n".join(addi_info))
            print("\n")


    def _build(self, inputs, sequence_length=None, initial_state=None):
        mode = "train" if self._is_training else "inference"

        if self._bidirectional:
            fw_cell = rnn_cell_utils.create_rnn_cell(
                unit_type=self._unit_type,
                num_units=self._num_units,
                num_layers=self._num_layers,
                mode=mode,
                dropout=self._dropout_rate,
                num_residual_layers=self._num_residual_layers,
                # use default cell creator
                single_cell_fn=None,
                **self._encoder_kargs)

            bw_cell = rnn_cell_utils.create_rnn_cell(
                unit_type=self._unit_type,
                num_units=self._num_units,
                num_layers=self._num_layers,
                mode=mode,
                dropout=self._dropout_rate,
                num_residual_layers=self._num_residual_layers,
                # use default cell creator
                single_cell_fn=None,
                **self._encoder_kargs)

            outputs, state = rnn_ops.bidirectional_dynamic_rnn(
                cell_fw=fw_cell,
                cell_bw=bw_cell,
                inputs=inputs,
                sequence_length=sequence_length,
                initial_state_fw=initial_state,
                initial_state_bw=initial_state,
                dtype=dtypes.float32,
                time_major=False,
                scope=self._encoder_scope)
            
            # concatenate the forwards and backwards states
            outputs = array_ops.concat(axis=2, values=outputs)

            self._cell = [fw_cell, bw_cell]

        else:
            cell = rnn_cell_utils.create_rnn_cell(
                unit_type=self._unit_type,
                num_units=self._num_units,
                num_layers=self._num_layers,
                mode=mode,
                dropout=self._dropout_rate,
                num_residual_layers=self._num_residual_layers,
                # use default cell creator
                single_cell_fn=None,
                **self._encoder_kargs)

            outputs, state = rnn_ops.dynamic_rnn(
                cell=cell,
                inputs=inputs,
                sequence_length=sequence_length,
                initial_state=initial_state,
                dtype=dtypes.float32,
                time_major=False,
                scope=self._encoder_scope)

            self._cell = cell

        return outputs, state

    def _clone(self, name):
        return type(self)(unit_type=self._unit_type,
                          num_units=self._num_units,
                          num_layers=self._num_layers,
                          dropout_rate=self._dropout_rate,
                          num_residual_layers=self._num_residual_layers,
                          scope=name,
                          is_training=self._is_training,
                          bidirectional=self._bidirectional,
                          name=name,
                          **self._encoder_kargs)



def get_variable_scope_name(value):
    """Returns the name of the variable scope indicated by the given value.

    Args:
      value: String, variable scope, or object with `variable_scope` attribute
      (e.g., Sonnet module).

    Returns:
      The name (a string) of the corresponding variable scope.

    Raises:
      ValueError: If `value` does not identify a variable scope.
    """
    # If the object has a "variable_scope" property, use it.
    value = getattr(value, "variable_scope", value)
    if isinstance(value, tf.VariableScope):
        return value.name
    elif isinstance(value, six.string_types):
        return value
    else:
        raise ValueError("Not a variable scope: {}".format(value))


def get_variables_in_scope(scope, collection=tf.GraphKeys.TRAINABLE_VARIABLES):
    """Returns a tuple `tf.Variable`s in a scope for a given collection.

    Args:
      scope: `tf.VariableScope` or string to retrieve variables from.
      collection: Collection to restrict query to. By default this is
          `tf.Graphkeys.TRAINABLE_VARIABLES`, which doesn't include
          non-trainable variables such as moving averages.

    Returns:
      A tuple of `tf.Variable` objects.
    """
    scope_name = get_variable_scope_name(scope)

    if scope_name:
        # Escape the name in case it contains any "." characters. Add a closing
        # slash so we will not search any scopes that have this scope name as a
        # prefix.
        scope_name = re.escape(scope_name) + "/"

    return tuple(tf.get_collection(collection, scope_name))


def get_variables_in_module(module,
                            collection=tf.GraphKeys.TRAINABLE_VARIABLES):
    """Returns tuple of `tf.Variable`s declared inside an `snt.Module`.

    Note that this operates by searching the variable scope a module contains,
    and so does not know about any modules which were constructed elsewhere but
    used inside this module.

    Args:
      module: `snt.Module` instance to query the scope of.
      collection: Collection to restrict query to. By default this is
        `tf.Graphkeys.TRAINABLE_VARIABLES`, which doesn't include non-trainable
        variables such as moving averages.

    Returns:
      A tuple of `tf.Variable` objects.

    Raises:
      NotConnectedError: If the module is not connected to the Graph.
    """
    return module.get_variables(collection=collection)


def create_linear_initializer(input_size, dtype=tf.float32):
    """Returns a default initializer for weights of a linear module."""
    stddev = 1 / math.sqrt(input_size)
    return tf.truncated_normal_initializer(stddev=stddev, dtype=dtype)


def create_bias_initializer(unused_bias_shape, dtype=tf.float32):
    """Returns a default initializer for the biases of linear module."""
    return tf.zeros_initializer(dtype=dtype)



def _single_cell(unit_type,
                 num_units,
                 mode="train",
                 dropout=None,
                 residual_connection=False,
                 *args, **kargs):
    """Create an instance of a single RNN cell."""

    # Cell Type
    if unit_type == "lstm":
        single_cell = tf.nn.rnn_cell.BasicLSTMCell(
            num_units, *args, **kargs)

    elif unit_type == "gru":
        single_cell = tf.nn.rnn_cell.GRUCell(
            num_units, *args, **kargs)

    elif unit_type == "layer_norm_lstm":
        # dropout_keep_prob
        single_cell = tf.contrib.rnn.LayerNormBasicLSTMCell(
            num_units, layer_norm=True, *args, **kargs)

    elif unit_type == "classical_lstm":
        single_cell = tf.nn.rnn_cell.LSTMCell(
            num_units, *args, **kargs)

    else:
        raise ValueError("Unknown unit type %s !" % unit_type)

    # dropout (= 1 - keep_prob) is set to 0 during eval and infer
    if dropout is not None:
        dropout = dropout if mode == "train" else 0.0
        single_cell = tf.nn.rnn_cell.DropoutWrapper(
            cell=single_cell, input_keep_prob=(1.0 - dropout))


    # Residual
    if residual_connection:
        single_cell = tf.nn.rnn_cell.ResidualWrapper(single_cell)


    return single_cell


def _cell_list(unit_type,
               num_units,
               num_layers,
               mode="train",
               dropout=None,
               num_residual_layers=0,
               single_cell_fn=None,
               *args, **kargs):
    """Create a list of RNN cells."""
    if not single_cell_fn:
        single_cell_fn = _single_cell

    cell_list = []
    for i in range(num_layers):
        single_cell = single_cell_fn(
            unit_type=unit_type,
            num_units=num_units,
            mode=mode,
            dropout=dropout,
            residual_connection=(i >= num_layers - num_residual_layers),
            *args, **kargs)
        cell_list.append(single_cell)

    return cell_list


def create_rnn_cell(unit_type,
                    num_units,
                    num_layers,
                    mode,
                    dropout=None,
                    num_residual_layers=0,
                    single_cell_fn=None,
                    cell_wrapper=None,
                    cell_wrapper_scope=None,
                    *args, **kargs):
    """Create multi-layer RNN cell.

    Args:
      unit_type: string representing the unit type, i.e. "lstm".
      num_units: the depth of each unit.
      num_layers: number of cells.
      num_residual_layers: Number of residual layers from top to bottom. For
        example, if `num_layers=4` and `num_residual_layers=2`, the last 2 RNN
        cells in the returned list will be wrapped with `ResidualWrapper`.
      dropout: floating point value between 0.0 and 1.0:
        the probability of dropout.  this is ignored if `mode != TRAIN`.
      mode: either tf.contrib.learn.TRAIN/EVAL/INFER
      single_cell_fn: allow for adding customized cell.
        When not specified, we default to model_helper._single_cell
    Returns:
      An `RNNCell` instance.
    """
    cell_list = _cell_list(unit_type=unit_type,
                           num_units=num_units,
                           num_layers=num_layers,
                           mode=mode,
                           dropout=dropout,
                           num_residual_layers=num_residual_layers,
                           single_cell_fn=single_cell_fn,
                           *args, **kargs)

    if cell_wrapper and not callable(cell_wrapper):
        raise TypeError("Expect `cell_wrapper` to be callable, "
                        "found ", type(cell_wrapper))

    if len(cell_list) == 1:  # Single layer.
        if not cell_wrapper:
            return cell_list[0]
        return cell_wrapper(cell=cell_list[0], cell_scope=cell_wrapper_scope)
        
    else:  # Multi layers
        if not cell_wrapper:
            return tf.nn.rnn_cell.MultiRNNCell(cell_list)
        return cell_wrapper(cells=cell_list, cell_scopes=cell_wrapper_scope)


def get_last_layer_cell_state(cell_states):
    if isinstance(cell_states, rnn_cell_impl.LSTMStateTuple):
        return cell_states
    else:
        return cell_states[-1]



class CachedElmoModule(base.AbstractModule):
    """Does nothing, but ensures consistent behavior"""

    def __init__(self,
                 name="cached_elmo",
                 trainable=True,
                 num_elements=CACHED_ELMO_NUM_ELEMENTS):

        super(CachedElmoModule, self).__init__(name=name)
        self._trainable = trainable
        self._num_elements = num_elements
        self._initializer = utils.create_linear_initializer(num_elements)

    def _build(self, inputs):
        self._weight = tf.get_variable(
            "weight",
            shape=[1, self._num_elements, 1, 1],
            dtype=tf.float32,
            initializer=self._initializer,
            trainable=self._trainable)

        # Inputs =  [batch_size, num_elements, sequence_len, num_units]
        # Outputs = [batch_size, sequence_len, num_units]
        return tf.reduce_sum(inputs * self._weight, axis=1)


    def _clone(self, name):
        return type(self)(name=name,
                          trainable=self._trainable,
                          num_elements=self._num_elements)
