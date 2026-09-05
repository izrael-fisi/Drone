// SPDX-License-Identifier: GPL-3.0-or-later
// Research-only OpenVINS integration. No vehicle or map publication interface.
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <vector>
#include <nlohmann/json.hpp>
#include <boost/version.hpp>
#include <ceres/version.h>
#include "core/VioManager.h"
#include "state/State.h"
#include "state/StateHelper.h"
#include "utils/print.h"
#include "utils/sensor_data.h"
#include "passive_anchors.h"

using json = nlohmann::json;
namespace fs = std::filesystem;

int64_t timestamp(const json &v) {
  if (!v.is_number_integer()) throw std::runtime_error("Timestamp must be integer nanoseconds");
  auto n = v.get<int64_t>();
  if (n <= 0) throw std::runtime_error("Timestamp must be positive");
  return n;
}

Eigen::Vector3d vector3(const json &v) {
  if (!v.is_array() || v.size() != 3) throw std::runtime_error("Expected three-axis SI measurement");
  Eigen::Vector3d result;
  for (int i = 0; i < 3; ++i) {
    if (!v[i].is_number()) throw std::runtime_error("Non-numeric sensor measurement");
    result[i] = v[i].get<double>();
  }
  if (!result.allFinite()) throw std::runtime_error("Non-finite sensor measurement");
  return result;
}

template <typename T> json matrix(const Eigen::MatrixBase<T> &value) {
  if (!value.allFinite()) throw std::runtime_error("Non-finite estimator output");
  json result = json::array();
  for (int r = 0; r < value.rows(); ++r) {
    json row = json::array();
    for (int c = 0; c < value.cols(); ++c) row.push_back(value(r, c));
    result.push_back(row);
  }
  return result;
}

int main(int argc, char **argv) {
  if (argc == 2 && std::string(argv[1]) == "--version") {
    std::cout << json({{"source_revision", OPENVINS_REVISION}, {"opencv", CV_VERSION},
                      {"ceres", CERES_VERSION_STRING}, {"boost", BOOST_LIB_VERSION},
                      {"compiler", __VERSION__}, {"ros_enabled", false},
                      {"stream_input_schema", "vision_nav_openvins_stream_v1"},
                      {"pose_window_schema", "vision_nav_openvins_pose_window_v1"},
                      {"passive_anchor_schema", "vision_nav_openvins_passive_anchor_schedule_v1"},
                      {"anchor_control_schema", "vision_nav_openvins_anchor_control_v1"},
                      {"position_authority", false}}).dump() << '\n';
    return 0;
  }
  bool export_window = false, stream_input = false, invalid_args = argc < 4;
  std::string anchor_schedule_path;
  std::string control_epoch, control_output;
  int control_capacity = 0;
  for (int i = 4; i < argc; ++i) {
    const std::string option(argv[i]);
    if (option == "--pose-window" && !export_window) export_window = true;
    else if (option == "--stream-input" && !stream_input) stream_input = true;
    else if (option == "--anchor-schedule" && anchor_schedule_path.empty() && i+1 < argc)
      anchor_schedule_path = argv[++i];
    else if (option == "--anchor-control" && control_epoch.empty() && i+3 < argc) {
      const std::string capacity = argv[++i];
      if (capacity.size() != 1 || capacity[0] < '1' || capacity[0] > '4') invalid_args = true;
      else control_capacity = capacity[0]-'0';
      control_epoch = argv[++i];
      control_output = argv[++i];
      if (control_epoch.size() != 36 || control_epoch.find_first_not_of("0123456789abcdef-") != std::string::npos)
        invalid_args = true;
    }
    else invalid_args = true;
  }
  if (invalid_args || (!anchor_schedule_path.empty() && (!export_window || stream_input || control_capacity))
      || (control_capacity && (!export_window || fs::path(control_output) == fs::path(argv[3])))) {
    std::cerr << "Usage: vision-nav-openvins CONFIG.yaml EVENTS.jsonl NEW_OUTPUT.jsonl [--pose-window] [--stream-input] [--anchor-schedule FILE | --anchor-control CAPACITY EPOCH NEW_ACK_FILE]\n";
    return 2;
  }
  try {
    if (fs::exists(argv[3])) throw std::runtime_error("Refusing to overwrite evidence");
    auto parser = std::make_shared<ov_core::YamlParser>(argv[1]);
    ov_core::Printer::setPrintLevel("WARNING");
    ov_msckf::VioManagerOptions params;
    params.print_and_load(parser);
    if (!parser->successful()) throw std::runtime_error("Incomplete OpenVINS calibration/configuration");
    if (!params.use_klt || params.use_aruco || params.state_options.num_cameras != 1 || params.downsample_cameras)
      throw std::runtime_error("Research runner requires mono KLT, no tags, no implicit resize");
    if (std::abs(params.calib_camimu_dt) > .1) throw std::runtime_error("Camera/IMU delay outside replay envelope");
    // A fixed clock calibration makes the look-ahead requirement auditable.
    if (params.state_options.do_calib_camera_timeoffset)
      throw std::runtime_error("Calibrate timing offline; online time-offset changes not supported by this runner");
    if (export_window && (params.state_options.max_clone_size < 2 || params.state_options.max_clone_size > 32))
      throw std::runtime_error("Joint pose export requires max_clones in [2,32]");
    params.num_opencv_threads = 1;
    params.use_multi_threading_pubs = false;
    params.use_multi_threading_subs = false;
    auto vio = std::make_shared<ov_msckf::VioManager>(params);
    std::unique_ptr<PassivePoseAnchors> anchors;
    std::ofstream control_out;
    int64_t control_sequence = 0;
    if (control_capacity) {
      if (fs::exists(control_output)) throw std::runtime_error("Refusing to overwrite anchor acknowledgements");
      control_out.open(control_output);
      if (!control_out) throw std::runtime_error("Unable to open anchor acknowledgements");
      anchors = std::make_unique<PassivePoseAnchors>(vio->get_state(), control_capacity);
    }
    json anchor_actions = json::array();
    size_t next_anchor_action = 0;
    if (!anchor_schedule_path.empty()) {
      if (fs::file_size(anchor_schedule_path) > 131072) throw std::runtime_error("Anchor schedule exceeds 128 KiB");
      std::ifstream schedule_file(anchor_schedule_path);
      auto schedule = json::parse(schedule_file);
      if (schedule.at("schema_version") != "vision_nav_openvins_passive_anchor_schedule_v1"
          || !schedule.at("capacity").is_number_integer()) throw std::runtime_error("Invalid anchor schedule");
      int capacity = schedule.at("capacity").get<int>();
      if (capacity < 1 || capacity > 4) throw std::runtime_error("Invalid passive anchor capacity");
      anchors = std::make_unique<PassivePoseAnchors>(vio->get_state(), capacity);
      anchor_actions = schedule.at("actions");
      if (!anchor_actions.is_array() || anchor_actions.empty() || anchor_actions.size() > 1024)
        throw std::runtime_error("Expected 1..1024 scheduled anchor actions");
      int64_t previous = 0;
      for (const auto &action : anchor_actions) {
        int64_t at = timestamp(action.at("at_timestamp_ns"));
        if (at < previous || timestamp(action.at("anchor_timestamp_ns")) > at
            || (action.at("operation") != "retain" && action.at("operation") != "release"))
          throw std::runtime_error("Invalid anchor action ordering, time or operation");
        previous = at;
      }
    }
    std::ifstream event_file;
    if (!stream_input) event_file.open(argv[2]);
    std::istream &events = stream_input ? std::cin : event_file;
    std::ofstream out(argv[3]);
    if (!events || !out) throw std::runtime_error("Unable to open replay input/output");
    int64_t origin = 0, last_imu = 0, first_imu = 0, last_camera = 0;
    size_t line_number = 0, initialized_frames = 0;
    std::map<double, int64_t> camera_times;
    std::string line;
    while (std::getline(events, line)) {
      ++line_number;
      if (line.size() > 65536) throw std::runtime_error("Replay event exceeds limit");
      json e = json::parse(line);
      std::string type = e.at("type").get<std::string>();
      if (type == "anchor_control") {
        // Same input owner/order as sensors, but never an IMU/camera update.
        if (!control_capacity || e.size() != 6 || e.at("schema_version") != "vision_nav_openvins_anchor_control_v1"
            || e.at("worker_run_id") != control_epoch || timestamp(e.at("request_id")) <= control_sequence
            || timestamp(e.at("request_id")) > 1024 || (e.at("operation") != "retain" && e.at("operation") != "release"))
          throw std::runtime_error("Invalid anchor control identity, sequence or bounds");
        const int64_t anchor_ns = timestamp(e.at("anchor_timestamp_ns"));
        const std::string operation = e.at("operation");
        control_sequence = timestamp(e.at("request_id"));
        std::string reason;
        auto state = vio->get_state();
        const double anchor_time = double(anchor_ns-origin)/1e9;
        auto original = camera_times.find(anchor_time);
        if (!last_camera || !vio->initialized()
            || std::abs(state->_timestamp-double(last_camera-origin)/1e9) >= 1e-6) reason = "no_initialized_posterior";
        else if (operation == "retain") {
          if (anchors->anchors().count(anchor_ns)) reason = "already_retained";
          else if (anchors->anchors().size() >= anchors->capacity()) reason = "anchor_capacity_reached";
          else if (original == camera_times.end() || original->second != anchor_ns || !state->_clones_IMU.count(anchor_time))
            reason = "camera_clone_not_retained";
          else anchors->retain(anchor_ns, anchor_time);
        } else if (!anchors->anchors().count(anchor_ns)) reason = "anchor_not_retained";
        else anchors->release(anchor_ns);
        json active = json::array();
        for (const auto &[stamp, _] : anchors->anchors()) active.push_back(stamp);
        json ack = {{"schema_version", "vision_nav_openvins_anchor_ack_v1"}, {"worker_run_id", control_epoch},
          {"request_id", control_sequence}, {"operation", operation}, {"anchor_timestamp_ns", anchor_ns},
          {"posterior_timestamp_ns", last_camera ? json(last_camera) : json(nullptr)},
          {"input_line", line_number}, {"status", reason.empty() ? "applied" : "rejected"},
          {"reason", reason.empty() ? "ok" : reason}, {"active_anchor_timestamps_ns", active},
          {"capacity", control_capacity}, {"source_revision", OPENVINS_REVISION},
          {"position_authority", false}, {"navigation_mode", "NO_GLOBAL_FIX"}, {"px4_external_vision", "disabled"}};
        control_out << ack.dump() << '\n';
        control_out.flush();
        if (!control_out) throw std::runtime_error("Anchor acknowledgement write failed");
        continue;
      }
      int64_t ns = timestamp(e.at("timestamp_ns"));
      if (!origin) origin = ns;
      double t = double(ns - origin) / 1e9;
      if (type == "imu") {
        if (ns <= last_imu || (last_imu && ns-last_imu > 50000000))
          throw std::runtime_error("Reordered or gapped IMU input");
        ov_core::ImuData imu;
        imu.timestamp = t;
        imu.wm = vector3(e.at("angular_velocity_rad_s"));
        imu.am = vector3(e.at("specific_force_m_s2"));
        vio->feed_measurement_imu(imu);
        last_imu = ns;
        if (!first_imu) first_imu = ns;
      } else if (type == "camera") {
        if (ns <= last_camera || (last_camera && ns-last_camera > 250000000))
          throw std::runtime_error("Reordered or gapped camera input");
        double imu_target = ns + params.calib_camimu_dt * 1e9;
        if (!first_imu || first_imu > imu_target || last_imu < imu_target + 5000000 || last_imu > imu_target + 150000000)
          throw std::runtime_error("Camera lacks bounded bracketing IMU coverage");
        fs::path path = fs::path(argv[2]).parent_path() / e.at("image_path").get<std::string>();
        cv::Mat image = cv::imread(path.string(), cv::IMREAD_GRAYSCALE);
        const auto camera = params.camera_intrinsics.at(0);
        if (image.empty() || image.cols != camera->w() || image.rows != camera->h())
          throw std::runtime_error("Missing image or calibration/resolution mismatch");
        ov_core::CameraData data;
        data.timestamp = t;
        data.sensor_ids = {0};
        data.images = {image};
        data.masks = {params.use_mask ? params.masks.at(0) : cv::Mat::zeros(image.size(), CV_8UC1)};
        if (export_window) camera_times.emplace(t, ns);
        vio->feed_measurement_camera(data);
        last_camera = ns;
        auto state = vio->get_state();
        bool initialized = vio->initialized() && std::abs(state->_timestamp-t) < 1e-6;
        json applied_actions = json::array();
        while (next_anchor_action < anchor_actions.size()
               && timestamp(anchor_actions[next_anchor_action].at("at_timestamp_ns")) <= ns) {
          const auto &action = anchor_actions[next_anchor_action];
          if (timestamp(action.at("at_timestamp_ns")) != ns || !initialized)
            throw std::runtime_error("Anchor action requires an exact initialized camera update");
          int64_t anchor_ns = timestamp(action.at("anchor_timestamp_ns"));
          if (action.at("operation") == "retain") anchors->retain(anchor_ns, double(anchor_ns-origin)/1e9);
          else anchors->release(anchor_ns);
          applied_actions.push_back(action);
          ++next_anchor_action;
        }
        json record = {{"schema_version", "vision_nav_openvins_local_v1"}, {"timestamp_ns", ns},
          {"line", line_number}, {"source_revision", OPENVINS_REVISION}, {"navigation_mode", "NO_GLOBAL_FIX"},
          {"position_authority", false}, {"px4_external_vision", "disabled"},
          {"local_state", initialized ? "LOCAL_ESTIMATE" : "INITIALIZING_OR_NO_UPDATE"},
          {"local_T_imu", nullptr}, {"state_covariance", nullptr}};
        if (anchors) record["passive_anchor_actions"] = applied_actions;
        if (initialized) {
          ++initialized_frames;
          Eigen::Matrix4d pose = Eigen::Matrix4d::Identity();
          // OpenVINS stores R_global_to_imu in JPL convention; export a matrix to avoid quaternion ambiguity.
          pose.block<3,3>(0,0) = state->_imu->Rot().transpose();
          pose.block<3,1>(0,3) = state->_imu->pos();
          auto cov = ov_msckf::StateHelper::get_marginal_covariance(state, {state->_imu});
          record["local_T_imu"] = matrix(pose);
          record["velocity_local_m_s"] = matrix(state->_imu->vel());
          record["gyro_bias_rad_s"] = matrix(state->_imu->bias_g());
          record["accel_bias_m_s2"] = matrix(state->_imu->bias_a());
          record["state_covariance"] = matrix(cov);
          record["covariance_convention"] = "openvins_theta_I_p_G_v_G_bg_ba";
          record["covariance_validated_for_fusion"] = false;
          record["msckf_used_features"] = vio->get_good_features_MSCKF().size();
        }
        if (export_window) {
          record["pose_window"] = nullptr;
          if (initialized && !state->_clones_IMU.empty()) {
            json poses = json::array(), times = json::array();
            json normal_times = json::array(), anchor_times = json::array();
            std::map<int64_t, std::shared_ptr<ov_type::PoseJPL>> selected;
            std::vector<std::shared_ptr<ov_type::Type>> variables;
            for (const auto &[clone_time, clone] : state->_clones_IMU) {
              auto source_time = camera_times.find(clone_time);
              if (source_time == camera_times.end()) throw std::runtime_error("Clone has no exact source camera timestamp");
              selected.emplace(source_time->second, clone);
              normal_times.push_back(source_time->second);
            }
            if (anchors) for (const auto &[anchor_ns, anchor] : anchors->anchors()) {
              selected[anchor_ns] = anchor.pose;
              anchor_times.push_back(anchor_ns);
            }
            for (const auto &[source_time, clone] : selected) {
              Eigen::Matrix4d pose = Eigen::Matrix4d::Identity();
              pose.block<3,3>(0,0) = clone->Rot().transpose();
              pose.block<3,1>(0,3) = clone->pos();
              poses.push_back(matrix(pose));
              times.push_back(source_time);
              variables.push_back(clone);
            }
            const auto joint = ov_msckf::StateHelper::get_marginal_covariance(state, variables);
            record["pose_window"] = {{"schema_version", "vision_nav_openvins_pose_window_v1"},
              {"posterior_timestamp_ns", ns}, {"pose_timestamps_ns", times}, {"local_T_imu", poses},
              {"joint_covariance_native", matrix(joint)},
              {"covariance_convention", "per_pose_theta_imu_delta_p_local"},
              {"linearization", "current_pose_means_with_native_fej_filter_covariance"},
              {"contains_cross_covariance", true}, {"independent_across_snapshots", false},
              {"map_updates_fused", false}, {"covariance_calibrated", false}, {"position_authority", false}};
            if (anchors) {
              auto &window = record["pose_window"];
              window["schema_version"] = "vision_nav_openvins_pose_window_v2";
              window["normal_clone_timestamps_ns"] = normal_times;
              window["passive_anchor_timestamps_ns"] = anchor_times;
              window["passive_anchor_capacity"] = anchors->capacity();
              window["retention_policy"] = "native_passive_stochastic_clones_explicit_release";
            }
          }
          double retain_from = t - params.init_options.init_window_time - 1.0;
          if (!state->_clones_IMU.empty()) retain_from = std::min(retain_from, state->_clones_IMU.begin()->first);
          while (!camera_times.empty() && camera_times.begin()->first < retain_from) camera_times.erase(camera_times.begin());
        }
        out << record.dump() << '\n';
        out.flush();
        if (!out) throw std::runtime_error("Evidence write failed");
      } else throw std::runtime_error("Unknown sensor event; no ground-truth initialization accepted");
    }
    if (!events.eof()) throw std::runtime_error("Sensor log read failed");
    if (next_anchor_action != anchor_actions.size()) throw std::runtime_error("Unexecuted anchor actions at EOF");
    std::cerr << "Replay complete; initialized camera updates: " << initialized_frames << '\n';
    return initialized_frames ? 0 : 3;
  } catch (const std::exception &e) {
    std::cerr << "OpenVINS replay failed: " << e.what() << '\n';
    return 2;
  }
}
