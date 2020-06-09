from google.protobuf import text_format
import traceback
import oneflow.oneflow_internal as oneflow_internal
from oneflow.python.oneflow_export import oneflow_export
from oneflow.python.framework.job_build_and_infer_error import JobBuildAndInferError
import oneflow.core.common.error_pb2 as error_util
import oneflow.python.framework.hob as hob
import oneflow.python.framework.runtime_mode as rt_mode
import oneflow.core.operator.op_conf_pb2 as op_conf_util
import oneflow.core.job.placement_pb2 as placment_util
import oneflow.core.register.logical_blob_id_pb2 as logical_blob_id_util
from oneflow.core.job.job_pb2 import TrainConf
import oneflow.python.framework.c_api_util as c_api_util
import oneflow.python.framework.session_context as session_context
import oneflow.python.framework.remote_blob as remote_blob_util
import oneflow as flow
import ctypes

def lr_lbn_from_train_conf(var_op_conf, train_conf):
    lr_lbn = None
    if var_op_conf.variable_conf.model_name == "weight":
        lr_lbn = train_conf.primary_lr_lbn
    elif var_op_conf.variable_conf.model_name == "bias":
        lr_lbn = train_conf.secondary_lr_lbn
    else:
        lr_lbn = train_conf.primary_lr_lbn
    assert lr_lbn is not None
    assert lr_lbn != ""
    return lr_lbn

class Base(oneflow_internal.OptimizerBase):
    def __init__(self):
        self.build_func_ = None
        oneflow_internal.OptimizerBase.__init__(self)

    def Build(self, var_op_conf_txt, parallel_conf_txt, diff_lbi_of_var_out_txt, train_conf_txt):
        try:
            assert self.build_func_ is not None
            with rt_mode.ModeScope(rt_mode.GLOBAL_MODE):
                var_op_conf = text_format.Parse(var_op_conf_txt, op_conf_util.OperatorConf())
                parallel_conf = text_format.Parse(parallel_conf_txt, placment_util.ParallelConf())
                diff_lbi = text_format.Parse(diff_lbi_of_var_out_txt, logical_blob_id_util.LogicalBlobId())
                train_conf = text_format.Parse(train_conf_txt, TrainConf())

                job_name = c_api_util.JobBuildAndInferCtx_GetCurrentJobName()
                sess = session_context.GetDefaultSession()
                var = sess.TryGetVariableBlobOfJobFromStash(job_name, var_op_conf.name)
                var_diff = remote_blob_util.RemoteBlob(diff_lbi)
                lr_lbn = lr_lbn_from_train_conf(var_op_conf, train_conf)
                [op_name, blob_name] = lr_lbn.split("/")
                lr_lbi = logical_blob_id_util.LogicalBlobId()
                lr_lbi.op_name = op_name
                lr_lbi.blob_name = blob_name
                lr = remote_blob_util.RemoteBlob(lr_lbi)
                self.build_func_.__call__(var, var_diff, lr, var_op_conf, parallel_conf)
        except Exception:
            traceback.print_exc()

@oneflow_export("register_optimizer")
def register_optimizer(name):
    def decorator_(func):
        optimizer = Base()
        optimizer.build_func_ = func
        pyobj = ctypes.py_object(optimizer)
        ctypes.pythonapi.Py_IncRef(pyobj)
        error_str = oneflow_internal.RegisterOptimizer(name, optimizer)
        error = text_format.Parse(error_str, error_util.ErrorProto())
        if error.HasField("error_type"):
            raise JobBuildAndInferError(error)
    return decorator_

@register_optimizer("sgd")
def build_sgd(var, var_diff, lr, var_op_conf, parallel_conf):
    assert len(parallel_conf.device_name) == 1
    splits = parallel_conf.device_name[0].split(":")
    assert len(splits) == 3
    device_tag = splits[1]
    machine_device_ids = ":".join([splits[0], splits[2]])
    with flow.fixed_placement(device_tag, machine_device_ids):
        with flow.distribute.consistent_strategy():
            if var_diff.dtype != lr.dtype:
                lr = flow.cast(lr, dtype=var_diff.dtype)
            flow.assign(var, var - var_diff * lr)
