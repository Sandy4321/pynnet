from pynnet.base import *

__all__ = ['LayerStack']

class LayerStack(BaseObject):
    def __init__(self, layers):
        r"""
        Stack of layers that acts as a layer.

        Examples:
        >>> from pynnet.layers import *
        >>> l = LayerStack([ReshapeLayer((None, 1, 32, 32)), 
        ...                 ConvLayer((5,5), 4)])
        >>> l2 = LayerStack([SimpleLayer(1024, 1024), l])

        Tests:
        >>> l.layers
        [<pynnet.layers.conv.ReshapeLayer object at ...>, <pynnet.layers.conv.ConvLayer object at ...>]
        >>> ll = test_saveload(l)
        >>> ll.layers
        [<pynnet.layers.conv.ReshapeLayer object at ...>, <pynnet.layers.conv.ConvLayer object at ...>]
        """
        self.layers = layers

    def _save_(self, file):
        file.write('LS1')
        psave([l.__class__ for l in self.layers], file)
        for l in self.layers:
            l.savef(file)

    def _load_(self, file):
        c = file.read(3)
        if c != 'LS1':
            raise ValueError('wrong cookie for LayerStack')
        lclass = pload(file)
        self.layers = [c.loadf(file) for c in lclass]

    def build(self, input):
        self.input = input
        for l in self.layers:
            l.build(input)
            input = l.output
        self.output = l.output
        self.params = sum((l.params for l in self.layers), [])