#ifndef ONEFLOW_CORE_GRAPH_INPLACE_REGST_GRAPH_H_
#define ONEFLOW_CORE_GRAPH_INPLACE_REGST_GRAPH_H_

#include "oneflow/core/common/util.h"
#include "oneflow/core/register/register_desc.pb.h"
#include "oneflow/core/graph/graph.h"

namespace oneflow {

class InplaceRegstEdge;
class InplaceRegstNode final : public Node<InplaceRegstNode, InplaceRegstEdge> {
 public:
  OF_DISALLOW_COPY_AND_MOVE(InplaceRegstNode);
  explicit InplaceRegstNode(const RegstDescProto* regst_desc) : regst_desc_(regst_desc) {}
  ~InplaceRegstNode() = default;

  const RegstDescProto* regst_desc() const { return regst_desc_; }

 private:
  const RegstDescProto* regst_desc_;
};

class InplaceRegstEdge final : public Edge<InplaceRegstNode, InplaceRegstEdge> {
 public:
  OF_DISALLOW_COPY_AND_MOVE(InplaceRegstEdge);
  InplaceRegstEdge() = default;
  ~InplaceRegstEdge() = default;
};

class InplaceRegstGraph final : public Graph<const InplaceRegstNode, const InplaceRegstEdge> {
 public:
  OF_DISALLOW_COPY_AND_MOVE(InplaceRegstGraph);
  explicit InplaceRegstGraph(const HashSet<const RegstDescProto*>& regst_descs);

 private:
  std::function<InplaceRegstNode*(const RegstDescProto*)> MakeMutFindOrCreateNode();
};

}  // namespace oneflow

#endif  // ONEFLOW_CORE_GRAPH_INPLACE_REGST_GRAPH_H_
