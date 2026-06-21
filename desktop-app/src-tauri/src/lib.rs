mod commands;

use commands::{
    config_cmd::{
        delete_support_bundle, list_autonomy_readiness_reports,
        list_feature_method_benchmark_reports, list_field_evidence_reports, list_support_bundles,
        list_yaml_configs, read_support_bundle_details, read_yaml_config, reveal_support_bundle,
        write_yaml_config,
    },
    discovery::{discover_pi_devices, local_network_hints},
    drone::{build_drone_bundle, import_elevation_assets, import_map_file},
    profile::{load_devices, load_profile, load_regions, save_devices, save_profile, save_regions},
    satellite::{download_tiles, estimate_tiles},
    ssh::{
        ssh_capture_camera_frame, ssh_download_file, ssh_run_command, ssh_upload_directory,
        ssh_upload_files, ssh_upload_project, test_ssh_connection,
    },
};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            load_profile,
            save_profile,
            load_devices,
            save_devices,
            load_regions,
            save_regions,
            estimate_tiles,
            download_tiles,
            build_drone_bundle,
            import_map_file,
            import_elevation_assets,
            discover_pi_devices,
            local_network_hints,
            test_ssh_connection,
            ssh_run_command,
            ssh_upload_files,
            ssh_upload_directory,
            ssh_upload_project,
            ssh_download_file,
            ssh_capture_camera_frame,
            read_yaml_config,
            write_yaml_config,
            list_yaml_configs,
            list_autonomy_readiness_reports,
            list_field_evidence_reports,
            list_feature_method_benchmark_reports,
            list_support_bundles,
            reveal_support_bundle,
            delete_support_bundle,
            read_support_bundle_details,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Drone Vision Nav");
}
