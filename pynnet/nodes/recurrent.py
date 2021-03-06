from .base import *
from .simple import SimpleNode
from .utils import JoinNode
from pynnet.nlins import *

import warnings, copy

from theano.tensor.shared_randomstreams import RandomStreams

__all__ = ['DelayNode', 'RecurrentInput', 'RecurrentOutput', 'RecurrentWrapper']

class DelayNode(BaseNode):
    r"""
    This is a node that inserts a delay of its input in the graph.
    
    Examples:
    >>> x = T.fmatrix()
    >>> d = DelayNode(x, 3, numpy.array([[1, 2], [2, 2], [2, 1]], dtype='float32'))
    """
    def __init__(self, input, delay, init_vals, name=None):
        r"""
        >>> x = T.matrix()
        >>> d = DelayNode(x, 1, numpy.array([[1, 2, 3]], dtype='float32'))
        >>> d.delay
        1
        >>> d.memory.get_value()
        array([[ 1.,  2.,  3.]], dtype=float32)
        """
        BaseNode.__init__(self, [input], name)
        self.delay = delay
        self.memory = theano.shared(init_vals, 'delaymem')

    def transform(self, input):
        r"""
        Tests:
        >>> x = T.fmatrix('x')
        >>> d = DelayNode(x, 2, numpy.random.random((2, 2)).astype('float32'))
        >>> d.params
        []
        >>> f = theano.function([x], d.output, allow_input_downcast=True)
        >>> inp = numpy.random.random((5, 2)).astype('float32')
        >>> v = f(inp)
        >>> v.dtype
        dtype('float32')
        >>> v.shape
        (5, 2)
        >>> (d.memory.get_value() == inp[-2:]).all()
        True
        >>> (v[2:] == inp[:-2]).all()
        True
        """
        j = T.join(0, self.memory, input)
        self.memory.default_update = j[-self.delay:]
        return j[:-self.delay]

class PlaceholderNode(BaseNode):
    def __init__(self, name=None):
        BaseNode.__init__(self, [], name)

    def transform(self):
        r"""
        If your code calls this you made an error somewhere.
        """
        raise RuntimeError('PlaceholderNode is not a real node and cannot be used unreplaced')

class RecurrentMemory(PlaceholderNode):
    def __init__(self, val, name=None):
        PlaceholderNode.__init__(self, name)
        self.init_val = val
        self.subgraph = None
        self.shared = theano.shared(self.init_val.copy())

    def clear(self):
        r"""
        Clears the shared memory.
        """
        self.shared.set_value(self.init_val.copy())

    def replace(self, eq):
        r"""
        See the documentation for BaseNode.replace
        """
        if self in eq:
            return eq[self]
        else:
            # Don't copy.
            return self

    def __getstate__(self):
        r"""
        >>> mem = RecurrentMemory(numpy.zeros((2,)))
        >>> mem2 = test_saveload(mem)
        >>> hasattr(mem.shared, 'default_update')
        False
        >>> hasattr(mem2.shared, 'default_update')
        False
        >>> mem.shared.default_update = mem.shared + 1
        >>> mem2 = test_saveload(mem)
        >>> hasattr(mem.shared, 'default_update')
        True
        >>> hasattr(mem2.shared, 'default_update')
        False
        """
        state = PlaceholderNode.__getstate__(self)
        if hasattr(self.shared, 'default_update'):
            state['shared'] = copy.copy(self.shared)
            del state['shared'].default_update
        return state

class RecurrentNode(BaseNode):
    r"""
    Base node for all recurrent nodes (or almost all).

    Since the handling of the subgraph(s) in a recurrent context is
    tricky this node was designed to handle the trickyness while
    leaving most of the functionality to subclasses.  The interface is
    a bit painful but should be wrapped by subclasses to make it
    easier.
    """
    def __init__(self, sequences, non_sequences, mem_node, out_subgraph, 
                 nopad=False, name=None):
        r"""
        Tests:
        >>> x = T.fmatrix('x')
        >>> mem = RecurrentMemory(numpy.zeros((5,), dtype='float32'))
        >>> out = SimpleNode([x, mem], [5, 5], 5, dtype='float32')
        >>> mem.subgraph = out
        >>> r = RecurrentNode([x], [], mem, out)
        >>> r.n_seqs
        1
        """
        BaseNode.__init__(self, sequences+non_sequences, name)
        self.memory = mem_node
        self.n_seqs = len(sequences)
        self.sequences = [PlaceholderNode() for s in sequences]
        self.non_sequences = [PlaceholderNode() for ns in non_sequences]
        rep = dict(zip(self.inputs[:self.n_seqs], self.sequences))
        rep.update(zip(self.inputs[self.n_seqs:], self.non_sequences))
        self.out_subgraph = out_subgraph.replace(rep)
        if self.memory.subgraph is None:
            warnings.warn("RecurrentMemory should always have a subgraph, using output for now")
            mem_subgraph = out_subgraph
        else:
            mem_subgraph = self.memory.subgraph
        self.mem_subgraph = mem_subgraph.replace(rep)
        self.nopad = nopad
        self.local_params = list(set(self.out_subgraph.params +
                                     self.mem_subgraph.params))

    def clear(self):
        r"""
        Resets the memory to the initial value.
        """
        self.memory.clear()

    def transform(self, *inputs):
        r"""
        Tests:
        >>> x = T.fmatrix('x')
        >>> mem = RecurrentMemory(numpy.zeros((2,), dtype='float32'))
        >>> i = JoinNode([x, mem], 1)
        >>> out = SimpleNode(i, 5, 2, dtype='float32')
        >>> mem.subgraph = out
        >>> r = RecurrentNode([x], [], mem, out)
        >>> r.params
        [W0, b]
        >>> f = theano.function([x], r.output, allow_input_downcast=True)
        >>> v = f(numpy.random.random((4, 3)))
        >>> v.dtype
        dtype('float32')
        >>> v.shape
        (4, 2)
        >>> (r.memory.shared.get_value() == v[-1]).all()
        True
        """
        if self.nopad:
            def wrap(v):
                return InputNode(v)
        else:
            def wrap(v):
                return InputNode(T.unbroadcast(T.shape_padleft(v), 0),
                                 allow_complex=True)

        def f(*inps):
            seqs = [wrap(s) for s in inps[:self.n_seqs]]
            non_seqs = [InputNode(i) for i in inps[self.n_seqs:-1]]
            mem = wrap(inps[-1])
            rep = dict(zip(self.sequences, seqs))
            rep.update(dict(zip(self.non_sequences, non_seqs)))
            rep.update({self.memory: mem})
            gout = self.out_subgraph.replace(rep)
            gmem = self.mem_subgraph.replace(rep)
            
            if self.nopad:
                return gout.output, gmem.output
            else:
                return gout.output[0], gmem.output[0]

        outs,upds = theano.scan(f, sequences=inputs[:self.n_seqs],
                                non_sequences=inputs[self.n_seqs:],
                                outputs_info=[None, self.memory.shared])
        
        for s, u in upds.iteritems():
            s.default_update = u
        self.memory.shared.default_update = outs[1][-1]
        return outs[0]

class RecurrentInput(BaseNode):
    r"""
    Node used to mark the point where recurrent input is inserted.

    For use in conjunction with RecurrentOutput.  The tag parameter
    serves to match a RecurrentOutput with the corresponding
    RecurrentInput.  More than one recurrent loop can be nested as
    long as the nesting is proper and they do not share the same tag.
    
    Examples:
    >>> x = T.fmatrix()
    >>> tag = object()
    >>> rx = RecurrentInput(x, tag)
    >>> o = SimpleNode(rx, 5, 2)
    >>> ro = RecurrentOutput(o, tag, outshp=(2,))

    You can then use `ro` as usual for the rest of the graph.
    
    Attributes:
    `tag` -- (object, read-write) some object to match this
             RecurrentInput with its corresponding RecurrentOutput
    """
    def __init__(self, input, tag, name=None):
        r"""
        Tests:
        >>> x = T.fmatrix('x')
        >>> tag = object()
        >>> rx = RecurrentInput(x, tag)
        >>> o = SimpleNode(rx, 5, 2)
        >>> ro = RecurrentOutput(o, tag, outshp=(2,))
        """
        BaseNode.__init__(self, [input], name)
        self.tag = tag

class FakeNode(BaseNode):
    def __init__(self, input, o, attr, name=None):
        BaseNode.__init__(self, [input], name)
        self.o = o
        self.attr = attr

    class output(prop):
        def fget(self):
            return getattr(self.o, self.attr)

class RecurrentOutput(BaseNode):
    r"""
    See documentation for RecurrentInput.
    
    Note that this does not use RecurrentNode since it does horrible
    things to the graph.
    """
    def __init__(self, input, tag, outshp=None, mem_init=None, name=None,
                 dtype=theano.config.floatX):
        r"""
        Tests:
        >>> x = T.fmatrix('x')
        >>> tag = object()
        >>> rx = RecurrentInput(x, tag)
        >>> o = SimpleNode(rx, 5, 2)
        >>> ro = RecurrentOutput(o, tag, outshp=(2,))
        >>> ro.memory.get_value()
        array([ 0.,  0.])
        >>> ro.output == ro.rec_in.output
        False
        """
        BaseNode.__init__(self, [input], name)
        self.tag = tag
        self.mem_init = mem_init or numpy.zeros(outshp, dtype=dtype)
        self.memory = theano.shared(self.mem_init.copy(), name='memory')
        self._inp = cell(None)

    def clear(self):
        r"""
        Resets the memory to the initial value.
        """
        self.memory.set_value(self.mem_init.copy())

    def _walker(self, node):
        if node.tag == self.tag:
            if self._inp.val is not node:
                assert self._inp.val is None
                self._inp.val = node
    
    class output(prop):
        def fget(self):
            if 'output' not in self._cache:
                self._makegraph()
            return self._cache['output']

    class inpmem(prop):
        def fget(self):
            if 'inpmem' not in self._cache:
                self._makegraph()
            return self._cache['inpmem']

    class rec_in(prop):
        r"""
        This was an horrible idea, please do not use and subclass
        RecurrentNode instead.
        """
        def fget(self):
            import warnings
            if 'rec_in' not in self._cache:
                self._makegraph()
            return self._cache['rec_in']

    def _makegraph(self):
        self.walk(self._walker, RecurrentInput)
        
        assert self._inp.val is not None
        self._cache['rec_in'] = FakeNode(self._inp.val, self, 'inpmem')

        def f(inp, mem):
            i = InputNode(T.unbroadcast(T.shape_padleft(T.join(0,inp,mem)),0),
                          allow_complex=True)
            g = self.inputs[0].replace({self._inp.val: i})
            return g.output[0], i.output[0]
        
        outs, updt = theano.scan(f, sequences=[self._inp.val.inputs[0].output],
                                 outputs_info=[self.memory, None])
        
        for s, u in updt.iteritems():
            s.default_update = u
        self.memory.default_update = outs[0][-1]

        # clear for the next run
        self._inp.val = None
        self._cache['output'] = outs[0]
        self._cache['inpmem'] = outs[1]

class RecurrentWrapper(RecurrentNode):
    r"""
    This is a recurrent node with a one tap delay.  This means it
    gets it own output from the previous step in addition to the
    input provided at each step.

    The memory is automatically updated and starts with a zero fill.
    If you want to clear the memory at some point, use the clear()
    function.  It will work on any backend and with any shape.  You
    may have problems on the GPU (and maybe elsewhere) otherwise.

    The recurrent part of the graph is built by the provided
    `subgraph_builder` function.

    This wrapper will not work for subgraphs with more than one input
    or output at the moment.  There are plans to fix that in the
    future.
    """
    def __init__(self, input, subgraph_builder, mem_init=None, 
                 outshp=None, dtype=theano.config.floatX, name=None):
        r"""
        Tests:
        >>> x = T.fmatrix('x')
        >>> r = RecurrentWrapper(x, lambda x_n: SimpleNode(x_n, 10, 5, dtype='float32'),
        ...                      outshp=(5,), dtype='float32')
        >>> r.memory.shared.get_value()
        array([ 0.,  0.,  0.,  0.,  0.], dtype=float32)
        """
        if mem_init is None:
            mem_init = numpy.zeros(outshp, dtype=dtype)
        mem = RecurrentMemory(mem_init)
        i = JoinNode([input, mem], 1)
        mem.subgraph = subgraph_builder(i)
        RecurrentNode.__init__(self, [input], [], mem, mem.subgraph,
                               name=name)
