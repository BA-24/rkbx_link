use std::{collections::HashMap, fs::File, io::Read};
use crate::memory::Pointer;

use crate::log::ScopedLogger;

impl RekordboxOffsets {
    pub fn from_lines(lines: &[String], logger: &ScopedLogger) -> Result<RekordboxOffsets, String> {
        let mut rows = lines.iter().peekable();

        let rb_version = rows.next().ok_or("No lines left")?.to_string();

        logger.debug("Masterdeck index");
        let masterdeck_index = Pointer::from_string(
            rows.next().ok_or("Missing masterdeck index pointer")?,
            logger,
        )?;

        let mut sample_position = vec![];
        let mut current_bpm = vec![];
        let mut track_info = vec![];
        let mut anlz_path = vec![];
        let mut track_uuid = vec![];

        while rows.peek().is_some() {
            logger.debug("Current BPM");
            current_bpm.push(Pointer::from_string(
                rows.next().ok_or("Missing BPM pointer")?,
                logger,
            )?);
            logger.debug("Sample position");
            sample_position.push(Pointer::from_string(
                rows.next().ok_or("Missing sample position pointer")?,
                logger,
            )?);
            logger.debug("Track info");
            track_info.push(Pointer::from_string(
                rows.next().ok_or("Missing track info pointer")?,
                logger,
            )?);
            logger.debug("ANLZ path");
            anlz_path.push(Pointer::from_string(
                rows.next().ok_or("Missing ANLZ path pointer")?,
                logger,
            )?);
            // Optional, written as "#uuid <chain>" so other versions of rkbx_link skip it
            track_uuid.push(match rows.peek().and_then(|r| r.strip_prefix("uuid ")) {
                Some(chain) => {
                    logger.debug("Track UUID");
                    let p = Pointer::from_string(chain, logger)?;
                    rows.next();
                    Some(p)
                }
                None => None,
            });
        }

        Ok(RekordboxOffsets {
            rbversion: rb_version,
            sample_position,
            current_bpm,
            masterdeck_index,
            track_info,
            anlz_path,
            track_uuid,
        })
    }

    pub fn from_file(
        name: &str,
        logger: ScopedLogger,
    ) -> Result<HashMap<String, RekordboxOffsets>, String> {
        let Ok(mut file) = File::open(name) else {
            return Err(format!("Could not open offset file {name}"));
        };
        let mut contents = String::new();
        if file.read_to_string(&mut contents).is_err() {
            return Err(format!("Could not read offset file {name}"));
        }
        drop(file);

        let mut empty_line_count = 0;

        let mut map = HashMap::new();

        let mut lines = vec![];
        for line in contents.lines() {
            if line.is_empty() {
                empty_line_count += 1;
                if empty_line_count >= 2 && !lines.is_empty() {
                    let offsets = RekordboxOffsets::from_lines(&lines, &logger)?;
                    map.insert(offsets.rbversion.clone(), offsets);
                    lines.clear();
                }
            } else {
                empty_line_count = 0;
                if let Some(uuid_line) = line.strip_prefix("#uuid ") {
                    lines.push(format!("uuid {uuid_line}"));
                } else if !line.starts_with('#') {
                    lines.push(line.to_string());
                }
            }
        }

        Ok(map)
    }
}

#[derive(Clone, Debug)]
pub struct RekordboxOffsets {
    pub rbversion: String,
    pub masterdeck_index: Pointer,
    pub sample_position: Vec<Pointer>,
    pub current_bpm: Vec<Pointer>,
    pub track_info: Vec<Pointer>,
    pub anlz_path: Vec<Pointer>,
    /// Per deck: the loaded track's UUID, used to find its ANLZ file when the
    /// path above is empty (tracks analysed while loading never get one).
    pub track_uuid: Vec<Option<Pointer>>,
}




