// SPDX-License-Identifier: GPL-3.0-or-later
#include "map_frame_control.h"
#include <algorithm>
#include <stdexcept>
#include <openssl/evp.h>
#include "types/Landmark.h"

namespace {
using json = nlohmann::json;
void require(bool ok, const char *message) { if (!ok) throw std::runtime_error(message); }
std::string sha256(const std::string &text) {
  unsigned char bytes[EVP_MAX_MD_SIZE];
  unsigned int length = 0;
  require(EVP_Digest(text.data(), text.size(), bytes, &length, EVP_sha256(), nullptr) == 1 && length == 32,
          "Native snapshot hashing failed");
  const char *hex = "0123456789abcdef";
  std::string result;
  for (unsigned int i = 0; i < length; ++i) {
    result += hex[bytes[i] >> 4]; result += hex[bytes[i] & 15];
  }
  return result;
}
int64_t integer(const json &v) {
  require(v.is_number_integer() && !v.is_boolean(), "Expected positive bounded integer");
  if (v.is_number_unsigned()) require(v.get<uint64_t>() <= INT64_MAX, "Integer exceeds signed range");
  auto n = v.get<int64_t>();
  require(n > 0, "Expected positive integer");
  return n;
}
Eigen::MatrixXd read_matrix(const json &v, int rows, int cols) {
  require(v.is_array() && v.size() == static_cast<size_t>(rows), "Invalid matrix rows");
  Eigen::MatrixXd result(rows, cols);
  for (int r = 0; r < rows; ++r) {
    require(v[r].is_array() && v[r].size() == static_cast<size_t>(cols), "Invalid matrix columns");
    for (int c = 0; c < cols; ++c) {
      require(v[r][c].is_number(), "Expected numeric matrix");
      result(r, c) = v[r][c].get<double>();
    }
  }
  require(result.allFinite(), "Nonfinite matrix");
  return result;
}
template <class T> json encode(const Eigen::MatrixBase<T> &m) {
  require(m.allFinite(), "Nonfinite map-frame output");
  auto out = json::array();
  for (int r = 0; r < m.rows(); ++r) {
    auto row = json::array();
    for (int c = 0; c < m.cols(); ++c) row.push_back(m(r, c));
    out.push_back(row);
  }
  return out;
}
void binding(const json &v) {
  require(v.is_object() && v.size() == 7, "Incomplete map-frame provenance declaration");
  for (const char *key : {"fix_id", "map_release_id", "model_release_id", "calibration_id"}) {
    require(v.at(key).is_string(), "Invalid release identity");
    const auto text = v.at(key).get<std::string>();
    require(!text.empty() && text.size() <= 128
            && text.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-") == std::string::npos,
            "Invalid release identity");
  }
  require(v.at("uncertainty_sha256").is_string(), "Missing uncertainty identity");
  const auto sha = v.at("uncertainty_sha256").get<std::string>();
  require(sha.size() == 64 && sha.find_first_not_of("0123456789abcdef") == std::string::npos, "Invalid uncertainty digest");
  require(v.at("conditional_model") == "complete_native_state_and_future_innovations_v1"
          && v.at("uncertainty_status") == "research_unqualified", "Unsupported uncertainty declaration");
}
}  // namespace

MapFrameControl::MapFrameControl(std::shared_ptr<ov_msckf::State> state, std::string epoch)
    : state_(std::move(state)), epoch_(std::move(epoch)), frame_(state_) {}

nlohmann::json MapFrameControl::command(const json &e, int64_t posterior_ns, int64_t origin_ns,
                                      size_t input_line, bool initialized,
                                      const std::map<double, int64_t> &camera_times,
                                      const PassivePoseAnchors *anchors) {
  require(e.is_object() && e.at("type") == "map_frame_control"
          && e.at("schema_version") == "vision_nav_openvins_map_frame_control_v1"
          && e.at("worker_run_id") == epoch_, "Invalid map-frame command identity");
  const auto id = integer(e.at("request_id"));
  require(id > sequence_ && id <= 1024, "Map-frame sequence or budget mismatch");
  const std::string op = e.at("operation");
  require((op == "capture" && e.size() == 6) || (op == "initialize" && e.size() == 12)
          || (op == "clear" && e.size() == 5), "Invalid map-frame command fields");
  sequence_ = id;
  json ack = {{"schema_version", "vision_nav_openvins_map_frame_ack_v1"}, {"worker_run_id", epoch_},
    {"request_id", id}, {"operation", op}, {"input_line", input_line},
    {"posterior_timestamp_ns", posterior_ns ? json(posterior_ns) : json(nullptr)},
    {"position_authority", false}, {"navigation_mode", "NO_GLOBAL_FIX"}, {"px4_external_vision", "disabled"},
    {"status", "rejected"}, {"reason", "not_applied"}, {"snapshot", nullptr}};
  try {
    if (op == "clear") {
      frame_.clear(); pending_.reset(); active_id_ = 0; bindings_ = nullptr; reason_ = "explicitly_cleared";
    } else if (op == "capture") {
      require(initialized, "no_initialized_posterior");
      require(!frame_.active(), "map_frame_already_active");
      require(state_->max_covariance_size() <= 256, "complete_native_state_exceeds_control_bound");
      const int64_t query = integer(e.at("query_timestamp_ns"));
      std::vector<std::pair<std::string, std::shared_ptr<ov_type::Type>>> named;
      auto add = [&](const std::string &name, const std::shared_ptr<ov_type::Type> &v) {
        if (v && v->id() >= 0) named.emplace_back(name, v);
      };
      add("imu", state_->_imu);
      add("imu_dw", state_->_calib_imu_dw); add("imu_da", state_->_calib_imu_da);
      add("imu_tg", state_->_calib_imu_tg); add("gyro_to_imu", state_->_calib_imu_GYROtoIMU);
      add("accel_to_imu", state_->_calib_imu_ACCtoIMU); add("camera_timeoffset", state_->_calib_dt_CAMtoIMU);
      for (const auto &[i, v] : state_->_calib_IMUtoCAM) add("camera_extrinsic:" + std::to_string(i), v);
      for (const auto &[i, v] : state_->_cam_intrinsics) add("camera_intrinsic:" + std::to_string(i), v);
      for (const auto &[i, v] : state_->_features_SLAM) add("landmark:" + std::to_string(i), v);
      int query_offset = -1;
      for (const auto &[t, v] : state_->_clones_IMU) {
        auto source = camera_times.find(t);
        require(source != camera_times.end(), "clone_source_time_missing");
        add("clone:" + std::to_string(source->second), v);
        if (source->second == query) query_offset = v->id();
      }
      if (anchors) for (const auto &[ns, a] : anchors->anchors()) {
        add("passive:" + std::to_string(ns), a.pose);
        if (ns == query) query_offset = a.pose->id();
      }
      require(query_offset >= 0, "query_pose_not_retained");
      std::sort(named.begin(), named.end(), [](const auto &a, const auto &b) { return a.second->id() < b.second->id(); });
      CorrelatedMapFrame::Order order;
      json layout = json::array();
      for (const auto &[name, v] : named) {
        order.push_back(v);
        layout.push_back({{"name", name}, {"offset", v->id()}, {"size", v->size()}, {"value", encode(v->value())}});
      }
      auto snapshot = frame_.capture(order);
      json data = {{"snapshot_id", id}, {"posterior_timestamp_ns", posterior_ns},
        {"query_timestamp_ns", query}, {"query_variable_offset", query_offset}, {"dimension", snapshot.dimension()},
        {"layout", layout}, {"covariance_native", encode(snapshot.covariance())},
        {"covariance_convention", "ordered_complete_native_error_state"}, {"covariance_calibrated", false}};
      const auto encoded = data.dump();
      snapshot_sha256_ = sha256(encoded);
      ack["snapshot"] = {{"sha256", snapshot_sha256_}, {"canonical_json", encoded}};
      pending_ = std::move(snapshot);
      snapshot_id_ = id; query_ns_ = query; origin_ns_ = origin_ns;
    } else {
      require(pending_.has_value() && integer(e.at("snapshot_id")) == snapshot_id_, "unknown_or_consumed_snapshot");
      auto snapshot = std::move(*pending_);
      pending_.reset();  // One attempt per owned snapshot, including a failed attempt.
      require(e.at("snapshot_sha256") == snapshot_sha256_, "snapshot_digest_mismatch");
      require(initialized, "no_initialized_posterior");
      const auto expiry = integer(e.at("expires_at_ns"));
      require(expiry > posterior_ns && expiry >= query_ns_ && expiry - query_ns_ <= 300000000000LL,
              "invalid_evidence_expiry");
      binding(e.at("bindings"));
      frame_.initialize(snapshot, read_matrix(e.at("local_T_map"), 4, 4),
                        read_matrix(e.at("regression_native"), 6, snapshot.dimension()),
                        read_matrix(e.at("conditional_covariance_native"), 6, 6),
                        double(query_ns_ - origin_ns_) / 1e9, double(expiry - origin_ns_) / 1e9);
      active_id_ = id; active_query_ns_ = query_ns_; active_expiry_ns_ = expiry;
      bindings_ = e.at("bindings"); reason_ = "conditional_map_frame";
    }
    ack["status"] = "applied"; ack["reason"] = "ok";
  } catch (const std::exception &error) {
    ack["reason"] = std::string(error.what()).substr(0, 240);
  }
  ack["active_initialization_id"] = active_id_ ? json(active_id_) : json(nullptr);
  return ack;
}

nlohmann::json MapFrameControl::projection(int64_t posterior_ns, bool initialized) {
  json result = {{"schema_version", "vision_nav_native_map_projection_v1"}, {"worker_run_id", epoch_},
    {"timestamp_ns", posterior_ns}, {"position_authority", false}, {"covariance_calibrated", false},
    {"px4_external_vision", "disabled"}, {"status", "unavailable"}, {"reason", reason_},
    {"initialization_id", nullptr}};
  if (!initialized) {
    frame_.clear(); pending_.reset(); active_id_ = 0; bindings_ = nullptr;
    reason_ = "no_initialized_posterior";
  }
  if (frame_.active()) {
    try {
      auto value = frame_.project();
      require(posterior_ns < active_expiry_ns_, "map_evidence_expired");
      result.update({{"status", "conditional_projection"}, {"reason", "research_unqualified"},
        {"initialization_id", active_id_}, {"observation_timestamp_ns", active_query_ns_},
        {"expires_at_ns", active_expiry_ns_}, {"map_age_s", double(posterior_ns - active_query_ns_) / 1e9},
        {"map_T_imu", encode(value.map_T_imu)}, {"covariance", encode(value.covariance)},
        {"covariance_convention", "right_local_se3_rotation_then_translation"}, {"bindings", bindings_}});
      return result;
    } catch (const std::exception &error) {
      frame_.clear(); active_id_ = 0; bindings_ = nullptr;
      reason_ = std::string(error.what()).substr(0, 240);
    }
  }
  result["reason"] = reason_;
  return result;
}
