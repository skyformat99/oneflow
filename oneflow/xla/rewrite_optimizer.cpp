#include "glog/logging.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/str_split.h"
#include <string>
#include <vector>

#include "oneflow/core/job/job.pb.h"
#include "oneflow/core/job/job_builder.h"
#include "oneflow/xla/of2xla/xla_utility.h"
#include "oneflow/xla/of2xla/xla_graph.h"
#include "oneflow/xla/of2xla/xla_node_attr.h"
#include "oneflow/xla/rewrite_optimizer_util.h"
#include "oneflow/xla/rewrite_optimizer.h"

namespace oneflow {

// Rewrite model update ops to optimizer graphs
class OptimizerRewritor {
 public:
  OptimizerRewritor(const mola::XlaGraph &graph, Job *job)
      : graph_(graph), builder_(std::make_shared<JobBuilder>(job)) {}

  virtual void Run();

 private:
  OptimizerMode GetOptimizerModeIfModelUpdate(const mola::XlaNode *node) const;

  OperatorConf *BuildClipGradientOp(const std::string &node_name,
                                    const std::string &gradient,
                                    const std::string &total_instances,
                                    const ClipConf &clip_conf);

  OperatorConf *BuildOptimizerOp(
      const mola::XlaNode *node,
      const std::string &gradient,
      const std::string &total_instances,
      const std::string &learning_rate,
      std::unordered_map<std::string, std::string> *update_vars);

  std::vector<OperatorConf *> BuildAssignOps(
      const std::string &node_name,
      const std::unordered_map<std::string, std::string> &update_vars);

  std::vector<std::string> GetControlInOpNames(const mola::XlaNode *node) const;

  const mola::XlaGraph &graph_;
  std::shared_ptr<JobBuilder> builder_;
};

OptimizerMode OptimizerRewritor::GetOptimizerModeIfModelUpdate(
    const mola::XlaNode *node) const {
  if (node->op_type() == "NavieModelUpdate") {
    return OptimizerMode::kNaive;
  } else if (node->op_type() == "MomentumModelUpdate") {
    return OptimizerMode::kMomentum;
  } else if (node->op_type() == "RMSPropModelUpdate") {
    return OptimizerMode::kRMSProp;
  } else if (node->op_type() == "LARSModelUpdate") {
    return OptimizerMode::kLARS;
  } else if (node->op_type() == "AdamModelUpdate") {
    return OptimizerMode::kAdam;
  } else {
    return OptimizerMode::kInvalid;
  }
}

OperatorConf *OptimizerRewritor::BuildClipGradientOp(
                                const std::string &node_name,
                                const std::string &gradient,
                                const std::string &total_instances,
                                const ClipConf &clip_conf) {
  OperatorConf op_conf;
  op_conf.set_name(absl::StrCat(node_name, "-clip_gradient"));
  ClipGradientOpConf *conf = op_conf.mutable_clip_gradient_conf();
  conf->set_out("out");
  conf->set_gradient(gradient);
  conf->set_instance_num_diff(total_instances);
  conf->set_clip_norm(clip_conf.clip_by_global_norm().clip_norm());

  if (clip_conf.clip_by_global_norm().has_global_norm()) {
    float global_norm = clip_conf.clip_by_global_norm().global_norm();
    conf->set_global_norm(global_norm);
  }
  
  ParallelConf parallel_conf = builder_->GetParallelConf(node_name);
  builder_->AddOps(parallel_conf, {op_conf});
  return builder_->MutableOpConf(op_conf.name());
}

OperatorConf *OptimizerRewritor::BuildOptimizerOp(
                    const mola::XlaNode *node,
                    const std::string &gradient,
                    const std::string &total_instances,
                    const std::string &learning_rate,
                    std::unordered_map<std::string, std::string> *update_vars) {
  OptimizerMode mode = GetOptimizerModeIfModelUpdate(node);
  CHECK_NE(mode, OptimizerMode::kInvalid);
  OperatorConf op_conf = OptimizerParamBuilder::Build(
      mode, node, gradient, total_instances, learning_rate, update_vars);

  ParallelConf parallel_conf = builder_->GetParallelConf(node->op_name());
  builder_->AddOrMutOpsOnlyOnce(parallel_conf, {op_conf});
  return builder_->MutableOpConf(op_conf.name());
}

std::vector<OperatorConf *> OptimizerRewritor::BuildAssignOps(
            const std::string &node_name,
            const std::unordered_map<std::string, std::string> &update_vars) {
  std::vector<OperatorConf *> assign_ops;
  for (const auto &p : update_vars) {
    OperatorConf op_conf;
    op_conf.set_name(absl::StrCat(node_name, "-assign-", assign_ops.size()));
    op_conf.mutable_assign_conf()->set_ref(p.first);
    op_conf.mutable_assign_conf()->set_value(p.second);

    ParallelConf parallel_conf = builder_->GetParallelConf(node_name);
    builder_->AddOps(parallel_conf, {op_conf});
    assign_ops.push_back(builder_->MutableOpConf(op_conf.name()));
  }
  return std::move(assign_ops);
}

std::vector<std::string> OptimizerRewritor::GetControlInOpNames(
    const mola::XlaNode *node) const {
  const auto &op_conf = builder_->GetOpConf(node->op_name());
  std::vector<std::string> ctrl_in_op_names;
  for (const std::string &name : op_conf.ctrl_in_op_name()) {
    ctrl_in_op_names.push_back(name);
  }
  return std::move(ctrl_in_op_names);
}

void SetControlInOpNames(OperatorConf *op_conf,
                         const std::vector<std::string> &ctrl_in_op_names) {
  for (const std::string &name : ctrl_in_op_names) {
    // op_conf->mutable_ctrl_in_op_name()->Add()->assign(name);
    op_conf->add_ctrl_in_op_name(name);
  }
}

void OptimizerRewritor::Run() {
  for (const mola::XlaNode *node : graph_.Nodes()) {
    OptimizerMode mode = GetOptimizerModeIfModelUpdate(node);
    if (mode == OptimizerMode::kInvalid) {
      // Skip the node if it is not a model update node
      continue;
    }
    using mola::GetNodeAttr;
    using mola::GetNodeAttrAsString;
    std::string learning_rate = GetNodeAttrAsString(node, "learning_rate");
    std::string model_diff = GetNodeAttrAsString(node, "model_diff");
    std::string total_instances =
        GetNodeAttrAsString(node, "total_instance_num_diff");
    std::string train_step = GetNodeAttrAsString(node, "train_step");

    auto control_in_op_names = GetControlInOpNames(node);
    std::vector<OperatorConf *> operator_confs;
    const auto *user_conf = dynamic_cast<NormalModelUpdateOpUserConf *>(
        GetNodeAttr<PbMessage *>(node, "user_conf"));
    CHECK_NOTNULL(user_conf);
    // Create clip gradient operator if `has_clip_conf`
    if (user_conf->has_clip_conf()) {
      OperatorConf *clip_conf = BuildClipGradientOp(node->op_name(), model_diff,
                                                    total_instances,
                                                    user_conf->clip_conf());
      operator_confs.push_back(clip_conf);
      model_diff = absl::StrCat(clip_conf->name(), "/out");
    }

    std::unordered_map<std::string, std::string> update_vars;

    OperatorConf *optimizer_conf = BuildOptimizerOp(
        node, model_diff, total_instances, learning_rate, &update_vars);
    operator_confs.push_back(optimizer_conf);

    // Currently each model update operator will result in some extra assign
    // operands to update model and momentum etc.
    // BuildAssignOps(node->op_name(), update_vars);

    if (control_in_op_names.size() > 0) {
      for (OperatorConf *op_conf : operator_confs) {
        // control_in_op_names.push_back(op_conf->name());
        SetControlInOpNames(op_conf, control_in_op_names);
      }
    }
  }
}

void RewriteOptimizerGraph(const mola::XlaGraph &graph, Job *job) {
  OptimizerRewritor(graph, job).Run();
}

}  // namespace oneflow
