// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cmath>
#include <cstdint>
#include <map>
#include <stdexcept>
#include "state/State.h"
#include "state/StateHelper.h"

// Passive stochastic clones remain in the native covariance, but never in a
// measurement's H_order or the feature tracker's ordinary camera clone window.
class PassivePoseAnchors {
 public:
  struct Anchor {
    double camera_time;
    std::shared_ptr<ov_type::PoseJPL> pose;
  };

  PassivePoseAnchors(std::shared_ptr<ov_msckf::State> state, size_t capacity)
      : state_(std::move(state)), capacity_(capacity) {
    if (!state_ || capacity < 1 || capacity > 4)
      throw std::runtime_error("Passive anchor capacity must be in [1,4]");
  }

  void retain(int64_t timestamp_ns, double camera_time) {
    if (timestamp_ns <= 0 || !std::isfinite(camera_time) || anchors_.count(timestamp_ns))
      throw std::runtime_error("Invalid or duplicate passive anchor");
    if (anchors_.size() >= capacity_)
      throw std::runtime_error("Passive anchor capacity reached; explicit release required");
    auto source = state_->_clones_IMU.find(camera_time);
    if (source == state_->_clones_IMU.end())
      throw std::runtime_error("Passive anchor must be an exact retained camera clone");
    for (const auto &[_, anchor] : anchors_)
      if (anchor.camera_time == camera_time) throw std::runtime_error("Duplicate anchor camera time");
    auto pose = std::dynamic_pointer_cast<ov_type::PoseJPL>(
        ov_msckf::StateHelper::clone(state_, source->second));
    if (!pose) throw std::runtime_error("Passive anchor is not a pose");
    anchors_.emplace(timestamp_ns, Anchor{camera_time, pose});
  }

  void release(int64_t timestamp_ns) {
    auto found = anchors_.find(timestamp_ns);
    if (found == anchors_.end()) throw std::runtime_error("Unknown passive anchor release");
    ov_msckf::StateHelper::marginalize(state_, found->second.pose);
    anchors_.erase(found);
  }

  const std::map<int64_t, Anchor> &anchors() const { return anchors_; }
  size_t capacity() const { return capacity_; }

 private:
  std::shared_ptr<ov_msckf::State> state_;
  size_t capacity_;
  std::map<int64_t, Anchor> anchors_;
};
