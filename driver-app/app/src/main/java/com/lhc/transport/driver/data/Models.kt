package com.lhc.transport.driver.data

import com.google.gson.annotations.SerializedName

data class LoginRequest(val username: String, val password: String)

data class LoginResponse(
    @SerializedName("access_token") val accessToken: String,
    @SerializedName("user") val user: DriverSummary
)

data class DriverSummary(
    @SerializedName("id") val id: Int,
    @SerializedName("full_name") val fullName: String,
    val role: String = ""
)

data class TripsResponse(
    @SerializedName("driver") val driver: DriverSummary,
    @SerializedName("trips") val trips: List<BookingUpdate>
)

data class BookingsResponse(
    val bookings: List<BookingUpdate>
)

data class BookingUpdate(
    @SerializedName("id") val id: Int,
    @SerializedName("booking_code") val bookingCode: String,
    @SerializedName("status") val status: String,
    @SerializedName("purpose") val purpose: String,
    @SerializedName("passenger_count") val passengerCount: Int,
    @SerializedName("pickup_location") val pickupLocation: String,
    @SerializedName("dropoff_location") val dropoffLocation: String,
    @SerializedName("departure_date") val departureDate: String,
    @SerializedName("departure_time") val departureTime: String,
    @SerializedName("is_roundtrip") val isRoundtrip: Boolean,
    @SerializedName("return_date") val returnDate: String?,
    @SerializedName("return_time") val returnTime: String?,
    @SerializedName("remarks") val remarks: String?,
    @SerializedName("requester_name") val requesterName: String,
    @SerializedName("requester_phone") val requesterPhone: String?,
    @SerializedName("requested_by") val requestedBy: Int,
    @SerializedName("vehicle_type_needed") val vehicleTypeNeeded: String,
    @SerializedName("assigned_vehicle_id") val assignedVehicleId: Int?,
    @SerializedName("assigned_driver_id") val assignedDriverId: Int?,
    @SerializedName("vehicle_name") val vehicleName: String?,
    @SerializedName("plate_number") val plateNumber: String?,
    @SerializedName("driver_name") val driverName: String?,
    @SerializedName("coordinator_remarks") val coordinatorRemarks: String?,
    @SerializedName("start_mileage") val startMileage: Int?,
    @SerializedName("end_mileage") val endMileage: Int?,
    @SerializedName("distance_km") val distanceKm: Int?,
    @SerializedName("driver_on_the_way_at") val driverOnTheWayAt: String?,
    @SerializedName("driver_arrived_at") val driverArrivedAt: String?,
    @SerializedName("picked_up_at") val pickedUpAt: String?,
    @SerializedName("ssd_gate_out_at") val timeOut: String?,
    @SerializedName("ssd_gate_in_at") val timeIn: String?
)

data class ActionRequest(
    val action: String,
    @SerializedName("start_mileage") val startMileage: Int? = null,
    @SerializedName("end_mileage") val endMileage: Int? = null,
    val reason: String? = null
)

data class ActionResponse(
    val status: String? = null,
    @SerializedName("driver_arrived_at") val driverArrivedAt: String? = null,
    @SerializedName("picked_up_at") val pickedUpAt: String? = null
)

data class GateFixRequest(
    val latitude: Double,
    val longitude: Double,
    val accuracy: Double
)

data class GateResult(
    val finished: Boolean = false,
    val event: String? = null,
    @SerializedName("time_out") val timeOut: String? = null,
    @SerializedName("time_in") val timeIn: String? = null,
    @SerializedName("distance_m") val distanceM: Double? = null,
    val ignored: Boolean = false,
    val message: String? = null
)

data class LogoutResponse(val logged_out: Boolean)

data class BookingRequest(
    val purpose: String,
    @SerializedName("passenger_count") val passengerCount: Int,
    @SerializedName("vehicle_type_needed") val vehicleTypeNeeded: String,
    @SerializedName("pickup_location") val pickupLocation: String,
    @SerializedName("dropoff_location") val dropoffLocation: String,
    @SerializedName("departure_date") val departureDate: String,
    @SerializedName("departure_time") val departureTime: String,
    @SerializedName("is_roundtrip") val isRoundtrip: Boolean,
    @SerializedName("return_date") val returnDate: String,
    @SerializedName("return_time") val returnTime: String,
    val remarks: String,
    @SerializedName("flight_type") val flightType: String = "none",
    @SerializedName("flight_number") val flightNumber: String = "",
    @SerializedName("flight_date") val flightDate: String = "",
    @SerializedName("flight_time") val flightTime: String = ""
)

data class NewBookingResponse(
    val id: Int,
    @SerializedName("booking_code") val bookingCode: String,
    val status: String
)

data class VehicleOption(
    val id: Int,
    @SerializedName("plate_number") val plateNumber: String,
    @SerializedName("vehicle_name") val vehicleName: String,
    @SerializedName("vehicle_type") val vehicleType: String,
    val capacity: Int,
    val status: String
)

data class DriverOption(
    val id: Int,
    @SerializedName("full_name") val fullName: String
)

data class CoordinatorResources(
    val vehicles: List<VehicleOption>,
    val drivers: List<DriverOption>,
    @SerializedName("vehicle_types") val vehicleTypes: List<String>
)

data class ReviewRequest(
    val action: String,
    @SerializedName("coordinator_remarks") val coordinatorRemarks: String = "",
    @SerializedName("vehicle_id") val vehicleId: Int? = null,
    @SerializedName("driver_id") val driverId: Int? = null
)
