import { ReloadOutlined } from '@ant-design/icons'
import { Button, DatePicker, Segmented, Space, Tooltip } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { PRESETS, type Preset } from '../time/timeRange'
import { useTimeRange } from '../time/TimeRangeContext'

const { RangePicker } = DatePicker

export function TimeRangeSelector() {
  const { range, preset, setPreset, setCustom, refresh } = useTimeRange()

  const pickerValue: [Dayjs, Dayjs] | null = preset
    ? null
    : [dayjs.unix(range.start), dayjs.unix(range.end)]

  return (
    <Space wrap>
      <Segmented<Preset | ''>
        options={PRESETS.map((p) => ({ label: p, value: p }))}
        value={preset ?? ''}
        onChange={(v) => {
          if (v) setPreset(v as Preset)
        }}
      />
      <RangePicker
        showTime
        allowClear={false}
        value={pickerValue}
        placeholder={['自定义开始', '自定义结束']}
        onChange={(vals) => {
          if (vals && vals[0] && vals[1]) {
            setCustom(vals[0].unix(), vals[1].unix())
          }
        }}
      />
      <Tooltip title="刷新">
        <Button icon={<ReloadOutlined />} onClick={refresh} />
      </Tooltip>
    </Space>
  )
}

export default TimeRangeSelector
