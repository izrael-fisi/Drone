// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <optional>
#include <nlohmann/json.hpp>
#include "correlated_map_frame.h"
#include "passive_anchors.h"

class MapFrameControl {
 public:
  MapFrameControl(std::shared_ptr<ov_msckf::State> state, std::string epoch);
  nlohmann::json command(const nlohmann::json &request, int64_t posterior_ns, int64_t origin_ns,
                        size_t input_line, bool initialized, const std::map<double, int64_t> &camera_times,
                        const PassivePoseAnchors *anchors);
  nlohmann::json projection(int64_t posterior_ns, bool initialized);
 private:
  std::shared_ptr<ov_msckf::State> state_;
  std::string epoch_;
  CorrelatedMapFrame frame_;
  std::optional<CorrelatedMapFrame::Snapshot> pending_;
  int64_t sequence_ = 0, snapshot_id_ = 0, query_ns_ = 0, origin_ns_ = 0, active_id_ = 0;
  int64_t active_query_ns_ = 0, active_expiry_ns_ = 0;
  nlohmann::json bindings_ = nullptr;
  std::string reason_ = "not_initialized";
  std::string snapshot_sha256_;
};
