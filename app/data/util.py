from typing import Dict, Any
import datetime

def parse_insert_timestamp(data: Dict[Any, Any]) -> Dict[Any, Any]:
  date = datetime.datetime.now(tz=datetime.timezone.utc)
  new_data = {
    "created_at": date,
    "updated_at": date
  }
  new_data.update(data)
  return new_data

def parse_update_timestamp(data: Dict[Any, Any]) -> Dict[Any, Any]:
  date = datetime.datetime.now(tz=datetime.timezone.utc)
  new_data = {
    "updated_at": date
  }
  new_data.update(data)
  return new_data
