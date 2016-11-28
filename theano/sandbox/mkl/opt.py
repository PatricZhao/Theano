"""
Optimizations addressing the convolution for mkl
"""
from __future__ import absolute_import, print_function, division
# import theano
from theano import compile, gof
from theano.compile import optdb
# from theano.gof import local_optimizer
from theano.gof.opt import copy_stack_trace

# from theano.tensor.opt import register_specialize_device
# from theano.tensor import TensorType
# from theano.tensor import opt

# from theano.tensor import nnet

from theano.gof import (local_optimizer, Optimizer, toolbox)
# from theano.gof.opt import LocalMetaOptimizer

from theano.sandbox.mkl import mkl_optimizer, register_opt, mkl_seqopt, mkl_available
from theano.sandbox.mkl.mkl_dummy import dummy_op

from theano.sandbox.mkl.basic_ops import (U2IGrad,
                                          I2U,
                                          I2UGrad,
                                          U2IPool,
                                          U2IRelu,
                                          U2ILRN
                                          )
from theano.sandbox.mkl import mkl_relu
from theano.sandbox.mkl import mkl_pool
from theano.sandbox.mkl import mkl_lrn

from theano.tensor.signal import pool
from theano.tensor.nnet.nnet import (AbstractRelu, AbstractReluGrad)

from theano.tensor.nnet.lrn import (AbstractLRN, AbstractLRNGrad)

# uniq_id for all the mkl Ops, to differentiate different layer even they've same parameters.
uniq_id = 0

# global OPT
optdb.register('mkl_opt',
               mkl_seqopt,
               #optdb.__position__.get('add_destroy_handler', 49.5) - 1,
               0.09,
               'mkl')

# local OPT
mkl_seqopt.register('mkl_local_optimizations', mkl_optimizer, 47.5,
                    'fast_run', 'fast_compile', 'mkl')

# show how global OPT works in here.
# TODO: write the real code soon


class Cut_I2U_U2I(Optimizer):
    def __init__(self):
        Optimizer.__init__(self)

    def add_requirements(self, fgraph):
        fgraph.attach_feature(toolbox.ReplaceValidate())

    def apply(self, fgraph):
        print("@global opt:Cut_I2U_U2I ")
        list_i2u = ['I2U']
        list_u2i = ['U2IPool', 'U2IRelu', 'U2IConv']
        list_i2u_back = ['I2UGrad']
        list_u2i_back = ['U2IGrad']
        list_forward = ['Relu', 'Pool', 'Conv']
        list_backward = ['ReluGrad', 'PoolGrad', 'ConvGrad']
        for node in fgraph.toposort():
            # backward
            if node.op.__class__.__name__ in list_i2u_back:
                out = node.outputs[0]
                for inp in node.inputs:
                    if isinstance(inp.owner, gof.Apply) and inp.owner.op.__class__.__name__ in list_u2i_back:
                        for inpOP in list(inp.owner.inputs):
                            if isinstance(inpOP.owner, gof.Apply) and inpOP.owner.op.__class__.__name__ in list_backward:
                                fgraph.replace_validate(out, inpOP.owner.outputs[0])
            # forward
            if node.op.__class__.__name__ in list_u2i:
                out  = node.outputs[0]
                for inp in node.inputs:
                    if isinstance(inp.owner, gof.Apply) and inp.owner.op.__class__.__name__ in list_i2u:
                        for inpOP in list(inp.owner.inputs):
                            if isinstance(inpOP.owner, gof.Apply) and inpOP.owner.op.__class__.__name__ in list_forward:
                                fgraph.replace_validate(out, inpOP.owner.outputs[0])



mkl_seqopt.register('Cut_I2U_U2I', Cut_I2U_U2I(),
                    59,
                    'fast_run',
                    'fast_compile',
                    'mkl')  # TODO: how to make it mandatory for gpu_seqopt?


# Local OPT
@register_opt()
@local_optimizer([dummy_op])
def local_dummy(node):
    print("Intel Theano local OPT: local_dummy")
    return False


@register_opt()
@local_optimizer([pool.Pool])
def local_pool_mkl(node):
    global uniq_id
    uniq_id += 1

    if not mkl_available():
        return

    if not isinstance(node.op, pool.Pool):
        return

    if node.inputs[0].type.ndim != 4:
        return

    if node.op.mode not in ('max', 'average_inc_pad', 'average_exc_pad'):
        return

    if not node.op.ignore_border:
        return

    x, ws, stride, pad = node.inputs
    if stride is None:
        stride = ws

    x_u2i = U2IPool(ignore_border=node.op.ignore_border,
                    mode=node.op.mode,
                    uniq_id=uniq_id)(x, ws, stride, pad)
    poolOut = mkl_pool.Pool(ignore_border=node.op.ignore_border,
                            mode=node.op.mode,
                            uniq_id=uniq_id)(x_u2i, ws, stride, pad)
    z_i2u = I2U(uniq_id=uniq_id)(poolOut)

    print ('@@@ done with local_pool_mkl')
    rval = z_i2u
    return [rval]


@register_opt()
@local_optimizer([pool.MaxPoolGrad])
def local_poolGrad_mkl(node):
    global uniq_id
    uniq_id += 1

    if not mkl_available():
        return

    if not isinstance(node.op, pool.MaxPoolGrad):
        return

    if node.inputs[0].type.ndim != 4:
        return

    if node.op.mode not in ('max', 'average_inc_pad', 'average_exc_pad'):
        return 

    # currently, MKL only support this mode
    if not node.op.ignore_border:
        return

    x, maxout, gz, ws, stride, pad = node.inputs
    if stride is None:
        stride = ws

    x_u2i = U2IPool(ignore_border=node.op.ignore_border,
                    mode=node.op.mode,
                    uniq_id=uniq_id)(x, ws, stride, pad)
    poolOut = mkl_pool.Pool(ignore_border=node.op.ignore_border,
                      mode=node.op.mode,
                      uniq_id=uniq_id)(x_u2i, ws, stride, pad)
    gz_u2i = I2UGrad(uniq_id=uniq_id)(poolOut, gz)

    poolGradOut = mkl_pool.PoolGrad(ignore_border=node.op.ignore_border,
                                    mode=node.op.mode,
                                    uniq_id=uniq_id)(x_u2i, gz_u2i, ws, stride, pad)

    gx_i2u = U2IGrad(uniq_id=uniq_id)(x, poolGradOut)

    print ('@@@ done with local_poolGrad_mkl')
    rval = gx_i2u
    return [rval]


@register_opt()
@local_optimizer([AbstractRelu])
def local_relu_mkl(node):
    global uniq_id
    uniq_id += 1

    if not mkl_available():
        return

    if not isinstance(node.op, AbstractRelu):
        return

    if node.inputs[0].type.ndim != 4:
        return

    x, = node.inputs

    x_u2i = U2IRelu(slope=node.op.slope, uniq_id=uniq_id)(x)
    reluOut = mkl_relu.Relu(slope=node.op.slope, uniq_id=uniq_id)(x_u2i)
    z_i2u = I2U(uniq_id=uniq_id)(reluOut)

    print ('@@@ done with local_relu_mkl')
    rval = z_i2u
    return [rval]


@register_opt()
@local_optimizer([AbstractReluGrad])
def local_reluGrad_mkl(node):
    global uniq_id
    uniq_id += 1

    if not mkl_available():
        return

    if not isinstance(node.op, AbstractReluGrad):
        return

    if node.inputs[0].type.ndim != 4:
        return

    x, gz = node.inputs


    x_u2i = U2IRelu(slope=node.op.slope, uniq_id=uniq_id)(x)
    reluOut = mkl_relu.Relu(slope=node.op.slope, uniq_id=uniq_id)(x_u2i)
    gz_u2i = I2UGrad(uniq_id=uniq_id)(reluOut, gz)

    reluGradOut = mkl_relu.ReluGrad(slope=node.op.slope, uniq_id=uniq_id)(x_u2i, gz_u2i)

    gx_i2u = U2IGrad(uniq_id=uniq_id)(x, reluGradOut)

    print ('@@@ done with local_reluGrad_mkl')
    rval = gx_i2u
    return [rval]


@register_opt()
@local_optimizer([AbstractLRN])
def local_lrn_mkl(node):
    print('@@local_lrn_mkl')
    global uniq_id
    uniq_id += 1

    if not mkl_available():
        return

    if not isinstance(node.op, AbstractLRN):
        return

    x, = node.inputs
    x_u2i = U2ILRN(slope=node.op.slope,
                   uniq_id=uniq_id,
                   alpha=node.op.alpha,
                   beta=node.op.beta,
                   k=node.op.k,
                   n=node.op.n)(x)

    lrnout = mkl_lrn.LRN(uniq_id=uniq_id,
                         alpha=node.op.alpha,
                         beta=node.op.beta,
                         k=node.op.k,
                         n=node.op.n)(x_u2i)
    z_i2u = I2U(uniq_id=uniq_id)(lrnout)

    rval = z_i2u
    return [rval]


@register_opt()
@local_optimizer([AbstractLRNGrad])
def local_lrnGrad_mkl(node):
    print('@@local_lrnGrad_mkl')
    global uniq_id
    uniq_id += 1

    if not mkl_available():
        return

    if not isinstance(node.op, AbstractLRNGrad):
        return

    x, gz, = node.inputs

    x_u2i = U2ILRN(slope=node.op.slope, 
                   uniq_id=uniq_id,
                   alpha=node.op.alpha,
                   beta=node.op.beta,
                   k=node.op.k,
                   n=node.op.n)(x)
    lrnOut = mkl_lrn.LRN(uniq_id=uniq_id,
                         alpha=node.op.alpha,
                         beta=node.op.beta,
                         k=node.op.k,
                         n=node.op.n)(x_u2i)
    gz_u2i = I2UGrad(uniq_id=uniq_id)(lrnOut, gz)
    lrnGradOut = mkl_lrn.LRNGrad(uniq_id=uniq_id,
                                            alpha=node.op.alpha,
                                            beta=node.op.beta,
                                            k=node.op.k,
                                            n=node.op.n,
                                            fp=node.op.fp)(x_u2i, gz_u2i)
    gx_i2u = U2IGrad(uniq_id=uniq_id)(x, lrnGradOut)

    rval = gx_i2u
    return [rval]