package com.lhc.transport.driver.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import com.lhc.transport.driver.data.BookingUpdate
import com.lhc.transport.driver.data.BookingRequest
import com.lhc.transport.driver.data.CoordinatorResources
import com.lhc.transport.driver.data.DriverApiService
import com.lhc.transport.driver.data.ReviewRequest
import com.lhc.transport.driver.data.VehicleOption
import com.lhc.transport.driver.data.DriverOption
import com.lhc.transport.driver.location.GateTrackingService
import kotlinx.coroutines.launch

@Composable
fun DriverApp() {
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            DriverLoginScreen()
        }
    }
}

@Composable
private fun DriverLoginScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var serverUrl by remember { mutableStateOf("") }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var token by remember { mutableStateOf<String?>(null) }
    var driverName by remember { mutableStateOf("") }
    var userRole by remember { mutableStateOf("") }
    var trips by remember { mutableStateOf<List<BookingUpdate>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var odometerAction by remember { mutableStateOf<Pair<BookingUpdate, String>?>(null) }
    var odometerText by remember { mutableStateOf("") }
    var activeGateTrip by remember { mutableStateOf<Int?>(null) }
    var pendingGateTrip by remember { mutableStateOf<Int?>(null) }
    val authenticatedApi = remember(serverUrl, token) {
        token?.let { DriverApiService(serverUrl, it) }
    }

    fun signOut() {
        val oldToken = token
        token = null
        userRole = ""
        driverName = ""
        trips = emptyList()
        activeGateTrip = null
        pendingGateTrip = null
        context.startService(
            Intent(context, GateTrackingService::class.java).apply {
                action = GateTrackingService.ACTION_STOP
            }
        )
        if (oldToken != null) {
            scope.launch {
                try {
                    val response = DriverApiService(serverUrl, oldToken).logout()
                    if (!response.isSuccessful) {
                        message = "Signed out on this device, but server token revocation failed (${response.code()})."
                    }
                } catch (error: Exception) {
                    message = "Signed out on this device, but server token revocation could not be confirmed."
                }
            }
        }
    }

    fun refreshTrips() {
        val activeToken = token ?: return
        scope.launch {
            loading = true
            try {
                val response = DriverApiService(serverUrl, activeToken).fetchTrips()
                if (response.isSuccessful) {
                    trips = response.body()?.trips.orEmpty()
                    message = "Trips updated."
                } else {
                    message = "Unable to load trips (${response.code()})."
                }
            } catch (error: Exception) {
                message = error.message ?: "Unable to connect to the server."
            } finally {
                loading = false
            }
        }
    }

    fun performAction(trip: BookingUpdate, action: String, mileage: Int? = null) {
        val activeToken = token ?: return
        scope.launch {
            loading = true
            try {
                val service = DriverApiService(serverUrl, activeToken)
                val response = when (action) {
                    "start" -> service.action(trip.id, action, startMileage = mileage)
                    "complete" -> service.action(trip.id, action, endMileage = mileage)
                    else -> service.action(trip.id, action)
                }
                if (response.isSuccessful) {
                    message = when (action) {
                        "accept" -> "Trip ${trip.bookingCode} accepted."
                        "on_the_way" -> "Marked on the way."
                        "arrival" -> "Arrival confirmed."
                        "pickup" -> "Client pickup confirmed."
                        "start" -> "Trip started."
                        "complete" -> "Trip completed."
                        else -> "Trip updated."
                    }
                    refreshTrips()
                } else {
                    message = "Action failed (${response.code()})."
                }
            } catch (error: Exception) {
                message = error.message ?: "Unable to update trip."
            } finally {
                loading = false
            }
        }
    }

    fun beginGateTracking(bookingId: Int, activeToken: String) {
        val serviceIntent = Intent(context, GateTrackingService::class.java).apply {
            action = GateTrackingService.ACTION_START
            putExtra(GateTrackingService.EXTRA_SERVER_URL, serverUrl)
            putExtra(GateTrackingService.EXTRA_TOKEN, activeToken)
            putExtra(GateTrackingService.EXTRA_BOOKING_ID, bookingId)
        }
        ContextCompat.startForegroundService(context, serviceIntent)
        activeGateTrip = bookingId
        message = "SSD gate GPS is running with a visible notification. Keep location permission enabled."
    }

    val gatePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        val locationGranted =
            results[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
                ContextCompat.checkSelfPermission(
                    context, Manifest.permission.ACCESS_FINE_LOCATION
                ) == PackageManager.PERMISSION_GRANTED
        val notificationsGranted = Build.VERSION.SDK_INT < 33 ||
            results[Manifest.permission.POST_NOTIFICATIONS] == true ||
                ContextCompat.checkSelfPermission(
                    context, Manifest.permission.POST_NOTIFICATIONS
                ) == PackageManager.PERMISSION_GRANTED
        val bookingId = pendingGateTrip
        val activeToken = token
        if (locationGranted && notificationsGranted && bookingId != null && activeToken != null) {
            beginGateTracking(bookingId, activeToken)
        } else {
            message = "SSD GPS needs precise location and notification permission."
        }
        pendingGateTrip = null
    }

    fun startGateTracking(bookingId: Int, activeToken: String) {
        val permissions = mutableListOf(Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) {
            permissions.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        val missing = permissions.filter {
            ContextCompat.checkSelfPermission(context, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isEmpty()) {
            beginGateTracking(bookingId, activeToken)
        } else {
            pendingGateTrip = bookingId
            gatePermissionLauncher.launch(missing.toTypedArray())
        }
    }

    if (token == null) {
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            verticalArrangement = Arrangement.Center
        ) {
            Text("LHC Transport", style = MaterialTheme.typography.headlineMedium)
            Spacer(Modifier.height(8.dp))
            Text("Sign in with your active account. Driver, requester, and coordinator access is available. The server must use HTTPS.")
            Spacer(Modifier.height(16.dp))
            OutlinedTextField(
                value = serverUrl,
                onValueChange = { serverUrl = it },
                label = { Text("HTTPS server URL") },
                placeholder = { Text("https://transport.example.com") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text("Username") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Password") },
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(16.dp))
            Button(
                enabled = !loading && username.isNotBlank() && password.isNotBlank() &&
                    serverUrl.startsWith("https://", ignoreCase = true),
                onClick = {
                    loading = true
                    message = null
                    scope.launch {
                        try {
                            val response = DriverApiService(serverUrl).login(username.trim(), password)
                            if (response.isSuccessful) {
                                val result = response.body()
                                if (result == null) {
                                    message = "The server returned an empty login response."
                                } else {
                                    token = result.accessToken
                                    driverName = result.user.fullName
                                    userRole = result.user.role
                                    password = ""
                                    if (result.user.role == "driver") refreshTrips()
                                }
                            } else {
                                message = when (response.code()) {
                                    401 -> "Username or password is incorrect."
                                    403 -> "This account cannot use the mobile app."
                                    else -> "Login failed (${response.code()})."
                                }
                            }
                        } catch (error: Exception) {
                            message = error.message ?: "Unable to connect to the HTTPS server."
                        } finally {
                            loading = false
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(if (loading) "Signing in..." else "Sign in")
            }
            message?.let {
                Spacer(Modifier.height(12.dp))
                Text(it, color = MaterialTheme.colorScheme.error)
            }
        }
    } else if (userRole == "requester" && authenticatedApi != null) {
        RequesterHome(
            api = authenticatedApi,
            userName = driverName,
            onSignOut = { signOut() }
        )
    } else if (userRole in listOf("coordinator", "admin") && authenticatedApi != null) {
        CoordinatorHome(
            api = authenticatedApi,
            userName = driverName,
            onSignOut = { signOut() }
        )
    } else {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Column {
                    Text("Assigned Trips", style = MaterialTheme.typography.headlineSmall)
                    Text(driverName, style = MaterialTheme.typography.bodyMedium)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(enabled = !loading, onClick = { refreshTrips() }) {
                        Text("Refresh")
                    }
                    TextButton(onClick = {
                        signOut()
                    }) { Text("Sign out") }
                }
            }
            Text(
                "SSD GPS requires precise location and a foreground notification. Android may still stop tracking.",
                style = MaterialTheme.typography.bodySmall
            )
            message?.let {
                Spacer(Modifier.height(8.dp))
                Text(it, color = MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.height(12.dp))
            if (trips.isEmpty()) {
                Text("No assigned trips.")
            } else {
                LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    items(trips, key = { it.id }) { trip ->
                        DriverTripCard(
                            trip = trip,
                            gateTracking = activeGateTrip == trip.id,
                            action = { selected ->
                                when (selected) {
                                    "start", "complete" -> {
                                        odometerAction = trip to selected
                                        odometerText = ""
                                    }
                                    "gate" -> {
                                        val currentToken = token
                                        if (currentToken != null) {
                                            startGateTracking(trip.id, currentToken)
                                        }
                                    }
                                    "stop_gate" -> {
                                        context.startService(
                                            Intent(context, GateTrackingService::class.java).apply {
                                                action = GateTrackingService.ACTION_STOP
                                            }
                                        )
                                        activeGateTrip = null
                                        message = "SSD gate GPS stopped."
                                    }
                                    else -> performAction(trip, selected)
                                }
                            }
                        )
                    }
                }
            }
        }
    }

    odometerAction?.let { (trip, action) ->
        AlertDialog(
            onDismissRequest = { odometerAction = null },
            title = { Text(if (action == "start") "Start trip" else "Complete trip") },
            text = {
                OutlinedTextField(
                    value = odometerText,
                    onValueChange = { odometerText = it.filter(Char::isDigit) },
                    label = { Text(if (action == "start") "Start odometer (km)" else "End odometer (km)") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    val mileage = odometerText.toIntOrNull()
                    if (mileage == null || mileage <= 0) {
                        message = "Enter a positive odometer reading."
                    } else {
                        performAction(trip, action, mileage)
                        odometerAction = null
                    }
                }) { Text("Save") }
            },
            dismissButton = {
                TextButton(onClick = { odometerAction = null }) { Text("Cancel") }
            }
        )
    }
}

@Composable
private fun DriverTripCard(
    trip: BookingUpdate,
    gateTracking: Boolean,
    action: (String) -> Unit
) {
    Card(Modifier.fillMaxWidth()) {
        Column(
            Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp)
        ) {
            Text(trip.bookingCode, style = MaterialTheme.typography.titleLarge)
            Text(trip.status.replace('_', ' ').uppercase(),
                color = MaterialTheme.colorScheme.primary)
            Text("Purpose: ${trip.purpose}")
            Text("Passengers: ${trip.passengerCount}")
            Text("Pickup: ${trip.pickupLocation}")
            Text("Drop-off: ${trip.dropoffLocation}")
            Text("Departure: ${trip.departureDate} ${trip.departureTime}")
            Text("Requester: ${trip.requesterName}")
            trip.requesterPhone?.takeIf { it.isNotBlank() }?.let { Text("Contact: $it") }
            if (!trip.vehicleName.isNullOrBlank()) {
                Text("Vehicle: ${trip.vehicleName} (${trip.plateNumber.orEmpty()})")
            }
            trip.remarks?.takeIf { it.isNotBlank() }?.let { Text("Remarks: $it") }
            trip.timeOut?.let { Text("SSD Time OUT: $it") }
            trip.timeIn?.let { Text("SSD Time IN: $it") }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                when (trip.status) {
                    "assigned" -> Button(onClick = { action("accept") }) { Text("Accept") }
                    "accepted" -> Button(onClick = { action("on_the_way") }) { Text("On the way") }
                    "on_the_way", "in_progress" -> {
                        if (trip.driverArrivedAt == null) {
                            Button(onClick = { action("arrival") }) { Text("Arrived") }
                        }
                        if (trip.status == "in_progress") {
                            Button(onClick = { action("complete") }) { Text("Complete") }
                        }
                    }
                }
                if (trip.status in listOf("accepted", "on_the_way")
                    && (trip.driverArrivedAt == null || trip.pickedUpAt != null)
                ) {
                    Button(onClick = { action("start") }) { Text("Start trip") }
                }
                if (trip.driverArrivedAt != null && trip.pickedUpAt == null) {
                    Button(onClick = { action("pickup") }) { Text("Picked up") }
                }
            }

            if (trip.status in listOf("accepted", "on_the_way", "in_progress", "completed")
                && trip.timeIn == null
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { action(if (gateTracking) "stop_gate" else "gate") }) {
                        Text(if (gateTracking) "Stop SSD GPS" else "Start SSD GPS")
                    }
                    if (gateTracking) Text("Tracking", color = MaterialTheme.colorScheme.primary)
                }
            }
        }
    }
}

@Composable
private fun RequesterHome(
    api: DriverApiService,
    userName: String,
    onSignOut: () -> Unit
) {
                val scope = rememberCoroutineScope()
                var bookings by remember { mutableStateOf<List<BookingUpdate>>(emptyList()) }
                var purpose by remember { mutableStateOf("") }
                var passengers by remember { mutableStateOf("1") }
                var vehicleType by remember { mutableStateOf("Van") }
                var pickup by remember { mutableStateOf("") }
                var dropoff by remember { mutableStateOf("") }
                var departureDate by remember { mutableStateOf("") }
                var departureTime by remember { mutableStateOf("") }
                var roundtrip by remember { mutableStateOf(false) }
                var returnDate by remember { mutableStateOf("") }
                var returnTime by remember { mutableStateOf("") }
                var flightType by remember { mutableStateOf("none") }
                var flightMenuOpen by remember { mutableStateOf(false) }
                var flightNumber by remember { mutableStateOf("") }
                var flightDate by remember { mutableStateOf("") }
                var flightTime by remember { mutableStateOf("") }
                val flightLabels = linkedMapOf(
                    "none" to "No flight",
                    "domestic" to "Domestic (driver leaves 3 hours before)",
                    "international" to "International (driver leaves 4 hours before)"
                )
                var remarks by remember { mutableStateOf("") }
                var typeMenuOpen by remember { mutableStateOf(false) }
                var loading by remember { mutableStateOf(false) }
                var message by remember { mutableStateOf<String?>(null) }

                fun refresh() {
                    scope.launch {
                        loading = true
                        try {
                            val response = api.fetchRequesterBookings()
                            if (response.isSuccessful) {
                                bookings = response.body()?.bookings.orEmpty()
                                message = "Bookings updated."
                            } else {
                                message = "Unable to load bookings (${response.code()})."
                            }
                        } catch (error: Exception) {
                            message = error.message ?: "Unable to connect to the server."
                        } finally {
                            loading = false
                        }
                    }
                }

                LaunchedEffect(api) { refresh() }

                Column(Modifier.fillMaxSize().padding(16.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Column {
                            Text("My Transportation", style = MaterialTheme.typography.headlineSmall)
                            Text(userName, style = MaterialTheme.typography.bodyMedium)
                        }
                        TextButton(onClick = onSignOut) { Text("Sign out") }
                    }
                    message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        item {
                            Card(Modifier.fillMaxWidth()) {
                                Column(
                                    Modifier.padding(16.dp),
                                    verticalArrangement = Arrangement.spacedBy(9.dp)
                                ) {
                                    Text("Request a trip", style = MaterialTheme.typography.titleLarge)
                                    OutlinedTextField(
                                        purpose, { purpose = it }, label = { Text("Purpose *") },
                                        modifier = Modifier.fillMaxWidth()
                                    )
                                    OutlinedTextField(
                                        passengers, { passengers = it.filter(Char::isDigit) },
                                        label = { Text("Passengers *") },
                                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                                        modifier = Modifier.fillMaxWidth()
                                    )
                                    Column {
                                        OutlinedButton(onClick = { typeMenuOpen = true }) {
                                            Text("Vehicle type: $vehicleType")
                                        }
                                        DropdownMenu(
                                            expanded = typeMenuOpen,
                                            onDismissRequest = { typeMenuOpen = false }
                                        ) {
                                            listOf("Van", "Sedan", "SUV", "Bus").forEach { type ->
                                                DropdownMenuItem(
                                                    text = { Text(type) },
                                                    onClick = {
                                                        vehicleType = type
                                                        typeMenuOpen = false
                                                    }
                                                )
                                            }
                                        }
                                    }
                                    OutlinedTextField(
                                        pickup, { pickup = it }, label = { Text("Pickup location") },
                                        modifier = Modifier.fillMaxWidth()
                                    )
                                    OutlinedTextField(
                                        dropoff, { dropoff = it }, label = { Text("Drop-off location") },
                                        modifier = Modifier.fillMaxWidth()
                                    )
                                    Column {
                                        OutlinedButton(onClick = { flightMenuOpen = true }) {
                                            Text("Flight: ${flightLabels[flightType]}")
                                        }
                                        DropdownMenu(
                                            expanded = flightMenuOpen,
                                            onDismissRequest = { flightMenuOpen = false }
                                        ) {
                                            flightLabels.forEach { (value, label) ->
                                                DropdownMenuItem(
                                                    text = { Text(label) },
                                                    onClick = {
                                                        flightType = value
                                                        flightMenuOpen = false
                                                    }
                                                )
                                            }
                                        }
                                    }
                                    if (flightType == "none") {
                                        OutlinedTextField(
                                            departureDate, { departureDate = it },
                                            label = { Text("Departure date (YYYY-MM-DD) *") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                        OutlinedTextField(
                                            departureTime, { departureTime = it },
                                            label = { Text("Departure time (HH:MM)") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                    } else {
                                        OutlinedTextField(
                                            flightNumber, { flightNumber = it },
                                            label = { Text("Flight number") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                        OutlinedTextField(
                                            flightDate, { flightDate = it },
                                            label = { Text("Flight date (YYYY-MM-DD) *") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                        OutlinedTextField(
                                            flightTime, { flightTime = it },
                                            label = { Text("Flight departure time (HH:MM) *") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                    }
                                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                                        Checkbox(checked = roundtrip, onCheckedChange = { roundtrip = it })
                                        Text("Round trip")
                                    }
                                    if (roundtrip) {
                                        OutlinedTextField(
                                            returnDate, { returnDate = it },
                                            label = { Text("Return date (YYYY-MM-DD) *") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                        OutlinedTextField(
                                            returnTime, { returnTime = it },
                                            label = { Text("Return time (HH:MM)") },
                                            modifier = Modifier.fillMaxWidth()
                                        )
                                    }
                                    OutlinedTextField(
                                        remarks, { remarks = it }, label = { Text("Remarks / instructions") },
                                        modifier = Modifier.fillMaxWidth()
                                    )
                                    Button(
                                        enabled = !loading,
                                        onClick = {
                                            val count = passengers.toIntOrNull()
                                            val hasFlight = flightType != "none"
                                            val missingDeparture = if (hasFlight) {
                                                flightDate.isBlank() || flightTime.isBlank()
                                            } else {
                                                departureDate.isBlank()
                                            }
                                            if (purpose.isBlank() || missingDeparture
                                                || count == null || count < 1
                                                || (roundtrip && returnDate.isBlank())
                                            ) {
                                                message = "Complete purpose, passenger count, departure (or flight) date and time, and round-trip return date."
                                            } else {
                                                scope.launch {
                                                    loading = true
                                                    try {
                                                        val response = api.createBooking(
                                                            BookingRequest(
                                                                purpose = purpose.trim(),
                                                                passengerCount = count,
                                                                vehicleTypeNeeded = vehicleType,
                                                                pickupLocation = pickup.trim(),
                                                                dropoffLocation = dropoff.trim(),
                                                                departureDate = departureDate.trim(),
                                                                departureTime = departureTime.trim(),
                                                                isRoundtrip = roundtrip,
                                                                returnDate = returnDate.trim(),
                                                                returnTime = returnTime.trim(),
                                                                remarks = remarks.trim(),
                                                                 flightType = flightType,
                                                                 flightNumber = flightNumber.trim(),
                                                                 flightDate = flightDate.trim(),
                                                                 flightTime = flightTime.trim()
                                                            )
                                                        )
                                                        if (response.isSuccessful) {
                                                            message = "Request ${response.body()?.bookingCode ?: ""} submitted."
                                                            purpose = ""
                                                            pickup = ""
                                                            dropoff = ""
                                                            departureDate = ""
                                                            departureTime = ""
                                                            returnDate = ""
                                                            returnTime = ""
                                                            flightType = "none"
                                                            flightNumber = ""
                                                            flightDate = ""
                                                            flightTime = ""
                                                            remarks = ""
                                                            refresh()
                                                        } else {
                                                            message = "Could not submit booking (${response.code()})."
                                                        }
                                                    } catch (error: Exception) {
                                                        message = error.message ?: "Could not submit booking."
                                                    } finally {
                                                        loading = false
                                                    }
                                                }
                                            }
                                        },
                                        modifier = Modifier.fillMaxWidth()
                                    ) { Text(if (loading) "Submitting..." else "Submit trip request") }
                                }
                            }
                        }
                        item {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Text("My requests", style = MaterialTheme.typography.titleLarge)
                                TextButton(enabled = !loading, onClick = { refresh() }) { Text("Refresh") }
                            }
                        }
                        items(bookings, key = { it.id }) { booking ->
                            RequesterBookingCard(
                                booking = booking,
                                onCancel = {
                                    scope.launch {
                                        loading = true
                                        try {
                                            val response = api.cancelBooking(booking.id)
                                            message = if (response.isSuccessful) {
                                                "Booking ${booking.bookingCode} cancelled."
                                            } else {
                                                "Could not cancel booking (${response.code()})."
                                            }
                                            refresh()
                                        } catch (error: Exception) {
                                            message = error.message ?: "Could not cancel booking."
                                        } finally {
                                            loading = false
                                        }
                                    }
                                }
                            )
                        }
                    }
                }
            }

            @Composable
            private fun RequesterBookingCard(booking: BookingUpdate, onCancel: () -> Unit) {
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text(booking.bookingCode, style = MaterialTheme.typography.titleMedium)
                        Text(booking.status.replace('_', ' ').uppercase(),
                            color = MaterialTheme.colorScheme.primary)
                        Text(booking.purpose)
                        Text("${booking.departureDate} ${booking.departureTime}")
                        Text("${booking.pickupLocation} → ${booking.dropoffLocation}")
                        booking.vehicleName?.let { Text("Vehicle: $it (${booking.plateNumber.orEmpty()})") }
                        booking.driverName?.let { Text("Driver: $it") }
                        booking.coordinatorRemarks?.takeIf { it.isNotBlank() }?.let {
                            Text("Coordinator: $it")
                        }
                        booking.driverOnTheWayAt?.let { Text("Driver on the way: $it") }
                        booking.driverArrivedAt?.let { Text("Driver arrived: $it") }
                        booking.pickedUpAt?.let { Text("Client picked up: $it") }
                        if (booking.status !in listOf("completed", "declined", "cancelled")) {
                            OutlinedButton(onClick = onCancel) { Text("Cancel booking") }
                        }
                    }
                }
            }

            @Composable
            private fun CoordinatorHome(
                api: DriverApiService,
                userName: String,
                onSignOut: () -> Unit
            ) {
                val scope = rememberCoroutineScope()
                var bookings by remember { mutableStateOf<List<BookingUpdate>>(emptyList()) }
                var resources by remember { mutableStateOf<CoordinatorResources?>(null) }
                var loading by remember { mutableStateOf(false) }
                var message by remember { mutableStateOf<String?>(null) }
                var reviewDialog by remember { mutableStateOf<Pair<BookingUpdate, String>?>(null) }
                var remarks by remember { mutableStateOf("") }
                var assignmentBooking by remember { mutableStateOf<BookingUpdate?>(null) }

                fun refresh() {
                    scope.launch {
                        loading = true
                        try {
                            val queue = api.fetchCoordinatorBookings()
                            val options = api.fetchCoordinatorResources()
                            if (queue.isSuccessful && options.isSuccessful) {
                                bookings = queue.body()?.bookings.orEmpty()
                                resources = options.body()
                                message = "Booking queue updated."
                            } else {
                                message = "Unable to load coordinator data (${queue.code()}/${options.code()})."
                            }
                        } catch (error: Exception) {
                            message = error.message ?: "Unable to connect to the server."
                        } finally {
                            loading = false
                        }
                    }
                }

                fun review(booking: BookingUpdate, action: String, note: String = "") {
                    scope.launch {
                        loading = true
                        try {
                            val response = api.reviewBooking(
                                booking.id,
                                ReviewRequest(action = action, coordinatorRemarks = note)
                            )
                            message = if (response.isSuccessful) {
                                when (action) {
                                    "approve" -> "${booking.bookingCode} approved."
                                    "decline" -> "${booking.bookingCode} rejected."
                                    else -> "${booking.bookingCode} updated."
                                }
                            } else {
                                "Action failed (${response.code()})."
                            }
                            if (response.isSuccessful) refresh()
                        } catch (error: Exception) {
                            message = error.message ?: "Could not update booking."
                        } finally {
                            loading = false
                        }
                    }
                }

                LaunchedEffect(api) { refresh() }

                Column(Modifier.fillMaxSize().padding(16.dp)) {
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Column {
                            Text("Coordinator Queue", style = MaterialTheme.typography.headlineSmall)
                            Text(userName, style = MaterialTheme.typography.bodyMedium)
                        }
                        Row {
                            TextButton(enabled = !loading, onClick = { refresh() }) { Text("Refresh") }
                            TextButton(onClick = onSignOut) { Text("Sign out") }
                        }
                    }
                    message?.let { Text(it, color = MaterialTheme.colorScheme.primary) }
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        items(bookings, key = { it.id }) { booking ->
                            Card(Modifier.fillMaxWidth()) {
                                Column(
                                    Modifier.padding(16.dp),
                                    verticalArrangement = Arrangement.spacedBy(6.dp)
                                ) {
                                    Text(booking.bookingCode, style = MaterialTheme.typography.titleMedium)
                                    Text(booking.status.replace('_', ' ').uppercase(),
                                        color = MaterialTheme.colorScheme.primary)
                                    Text("${booking.requesterName} · ${booking.passengerCount} passengers")
                                    Text(booking.purpose)
                                    Text("Vehicle needed: ${booking.vehicleTypeNeeded}")
                                    Text("${booking.pickupLocation} → ${booking.dropoffLocation}")
                                    Text("${booking.departureDate} ${booking.departureTime}")
                                    booking.vehicleName?.let {
                                        Text("Assigned: $it (${booking.plateNumber.orEmpty()})")
                                    }
                                    booking.driverName?.let { Text("Driver: $it") }
                                    booking.driverArrivedAt?.let { Text("Driver arrived: $it") }
                                    booking.pickedUpAt?.let { Text("Client picked up: $it") }
                                    booking.coordinatorRemarks?.takeIf { it.isNotBlank() }?.let {
                                        Text("Remarks: $it")
                                    }
                                    if (booking.status == "pending") {
                                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                            Button(
                                                enabled = !loading,
                                                onClick = { review(booking, "approve") }
                                            ) { Text("Approve") }
                                            OutlinedButton(
                                                enabled = !loading,
                                                onClick = {
                                                    remarks = ""
                                                    reviewDialog = booking to "decline"
                                                }
                                            ) { Text("Reject") }
                                        }
                                    } else if (booking.status in listOf("approved", "assigned")) {
                                        Button(
                                            enabled = !loading && resources != null,
                                            onClick = { assignmentBooking = booking }
                                        ) {
                                            Text(if (booking.status == "approved") "Assign vehicle & driver" else "Reassign")
                                        }
                                        if (booking.status == "approved") {
                                            OutlinedButton(
                                                enabled = !loading,
                                                onClick = {
                                                    remarks = ""
                                                    reviewDialog = booking to "decline"
                                                }
                                            ) { Text("Reject") }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                reviewDialog?.let { (booking, action) ->
                    AlertDialog(
                        onDismissRequest = { reviewDialog = null },
                        title = { Text("Reject ${booking.bookingCode}") },
                        text = {
                            OutlinedTextField(
                                remarks, { remarks = it },
                                label = { Text("Reason (required)") },
                                modifier = Modifier.fillMaxWidth()
                            )
                        },
                        confirmButton = {
                            TextButton(onClick = {
                                if (remarks.isBlank()) {
                                    message = "Enter a rejection reason."
                                } else {
                                    review(booking, action, remarks.trim())
                                    reviewDialog = null
                                }
                            }) { Text("Reject booking") }
                        },
                        dismissButton = {
                            TextButton(onClick = { reviewDialog = null }) { Text("Cancel") }
                        }
                    )
                }

                val currentResources = resources
                assignmentBooking?.let { booking ->
                    if (currentResources != null) {
                        CoordinatorAssignmentDialog(
                            booking = booking,
                            vehicles = currentResources.vehicles.filter {
                                it.vehicleType == booking.vehicleTypeNeeded &&
                                    it.capacity >= booking.passengerCount &&
                                    it.status !in listOf("maintenance", "inactive")
                            },
                            drivers = currentResources.drivers,
                            onDismiss = { assignmentBooking = null },
                            onAssign = { vehicleId, driverId, assignmentRemarks ->
                                scope.launch {
                                    loading = true
                                    try {
                                        val response = api.reviewBooking(
                                            booking.id,
                                            ReviewRequest(
                                                action = "assign",
                                                coordinatorRemarks = assignmentRemarks,
                                                vehicleId = vehicleId,
                                                driverId = driverId
                                            )
                                        )
                                        message = if (response.isSuccessful) {
                                            "${booking.bookingCode} assigned."
                                        } else {
                                            "Assignment failed (${response.code()})."
                                        }
                                        assignmentBooking = null
                                        if (response.isSuccessful) refresh()
                                    } catch (error: Exception) {
                                        message = error.message ?: "Could not assign booking."
                                    } finally {
                                        loading = false
                                    }
                                }
                            }
                        )
                    }
                }
            }

            @Composable
            private fun CoordinatorAssignmentDialog(
                booking: BookingUpdate,
                vehicles: List<VehicleOption>,
                drivers: List<DriverOption>,
                onDismiss: () -> Unit,
                onAssign: (Int, Int?, String) -> Unit
            ) {
                var vehicleId by remember(booking.id) {
                    mutableStateOf(vehicles.firstOrNull()?.id)
                }
                var driverId by remember(booking.id) {
                    mutableStateOf(booking.assignedDriverId)
                }
                var remarks by remember(booking.id) {
                    mutableStateOf(booking.coordinatorRemarks.orEmpty())
                }
                var vehicleMenu by remember { mutableStateOf(false) }
                var driverMenu by remember { mutableStateOf(false) }

                AlertDialog(
                    onDismissRequest = onDismiss,
                    title = { Text("Assign ${booking.bookingCode}") },
                    text = {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("${booking.vehicleTypeNeeded} · ${booking.passengerCount} passengers")
                            Column {
                                OutlinedButton(onClick = { vehicleMenu = true }) {
                                    val selected = vehicles.firstOrNull { it.id == vehicleId }
                                    Text(selected?.let {
                                        "${it.plateNumber} · ${it.vehicleName} (${it.capacity})"
                                    } ?: "Select vehicle")
                                }
                                DropdownMenu(
                                    expanded = vehicleMenu,
                                    onDismissRequest = { vehicleMenu = false }
                                ) {
                                    vehicles.forEach { vehicle ->
                                        DropdownMenuItem(
                                            text = {
                                                Text("${vehicle.plateNumber} · ${vehicle.vehicleName} (${vehicle.capacity})")
                                            },
                                            onClick = {
                                                vehicleId = vehicle.id
                                                vehicleMenu = false
                                            }
                                        )
                                    }
                                }
                            }
                            Column {
                                OutlinedButton(onClick = { driverMenu = true }) {
                                    Text(drivers.firstOrNull { it.id == driverId }?.fullName
                                        ?: "No driver selected")
                                }
                                DropdownMenu(
                                    expanded = driverMenu,
                                    onDismissRequest = { driverMenu = false }
                                ) {
                                    DropdownMenuItem(
                                        text = { Text("No driver selected") },
                                        onClick = {
                                            driverId = null
                                            driverMenu = false
                                        }
                                    )
                                    drivers.forEach { driver ->
                                        DropdownMenuItem(
                                            text = { Text(driver.fullName) },
                                            onClick = {
                                                driverId = driver.id
                                                driverMenu = false
                                            }
                                        )
                                    }
                                }
                            }
                            OutlinedTextField(
                                remarks, { remarks = it },
                                label = { Text("Coordinator remarks") },
                                modifier = Modifier.fillMaxWidth()
                            )
                            if (vehicles.isEmpty()) {
                                Text(
                                    "No matching active vehicle has enough seats.",
                                    color = MaterialTheme.colorScheme.error
                                )
                            }
                        }
                    },
                    confirmButton = {
                        TextButton(
                            enabled = vehicleId != null && vehicles.any { it.id == vehicleId },
                            onClick = {
                                vehicleId?.let { onAssign(it, driverId, remarks.trim()) }
                            }
                        ) { Text("Confirm assignment") }
                    },
                    dismissButton = {
                        TextButton(onClick = onDismiss) { Text("Cancel") }
                    }
                )
            }
